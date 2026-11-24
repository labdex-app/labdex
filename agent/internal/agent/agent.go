package agent

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"

	"github.com/labdex-app/labdex/agent/internal/host"
	"github.com/labdex-app/labdex/agent/internal/jobs"
	"github.com/labdex-app/labdex/agent/internal/protocol"
	"github.com/labdex-app/labdex/agent/internal/runner"
	"github.com/labdex-app/labdex/agent/internal/spool"
)

const (
	ConnectTimeout = 15 * time.Second
	LinkIdle       = 90 * time.Second // the hub answers every heartbeat: quiet this long, it's gone
	MaxBackoff     = 60 * time.Second
	HealthyFor     = 120 * time.Second
	BundleWait     = 120 * time.Second
	PingEvery      = 25 * time.Second
	Workers        = 2
)

// Refused means the hub won't take this agent (unknown or revoked key, root not allowed,
// protocol): stop, don't retry. The CLI exits 3 on it.
type Refused struct{ Reason string }

func (e *Refused) Error() string { return e.Reason }

// ----------------------------------------------------------------------------- audit and replay guard

// AuditLog is the agent's own record of every job: one JSON line each, never the job's output.
type AuditLog struct {
	path string
	mu   sync.Mutex
}

// Write appends one event; fields are key, value pairs, kept in order.
func (a *AuditLog) Write(event string, fields ...any) {
	var b bytes.Buffer
	ts := math.Round(float64(time.Now().UnixNano())/1e6) / 1e3
	fmt.Fprintf(&b, `{"ts":%s,"event":%s`, strconv.FormatFloat(ts, 'f', -1, 64), jsonText(event))
	for i := 0; i+1 < len(fields); i += 2 {
		fmt.Fprintf(&b, `,%s:%s`, jsonText(fields[i]), jsonText(fields[i+1]))
	}
	b.WriteString("}\n")
	a.mu.Lock()
	defer a.mu.Unlock()
	f, err := os.OpenFile(a.path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
	if err != nil {
		return // a full disk must not stop the agent; the hub keeps its own audit
	}
	_, _ = f.Write(b.Bytes())
	_ = f.Close()
}

func jsonText(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		b, _ = json.Marshal(fmt.Sprint(v))
	}
	return string(b)
}

// SeenJobs holds job ids already taken until they expire: a recorded job sent again is refused.
type SeenJobs struct {
	path string
	mu   sync.Mutex
	ids  map[string]float64
}

// OpenSeen reads seen.json.
func OpenSeen(path string) *SeenJobs {
	s := &SeenJobs{path: path, ids: map[string]float64{}}
	if b, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(b, &s.ids)
		if s.ids == nil {
			s.ids = map[string]float64{}
		}
	}
	return s
}

// Add is false when the id was seen already.
func (s *SeenJobs) Add(id string, expires float64, now time.Time) bool {
	t := float64(now.UnixNano()) / 1e9
	s.mu.Lock()
	defer s.mu.Unlock()
	for k, v := range s.ids {
		if v+protocol.MaxSkew <= t {
			delete(s.ids, k)
		}
	}
	if _, ok := s.ids[id]; ok {
		return false
	}
	s.ids[id] = expires
	if b, err := json.Marshal(s.ids); err == nil {
		_ = WritePrivate(s.path, b)
	}
	return true
}

// ----------------------------------------------------------------------------- the agent

// Agent keeps one outgoing link to the hub and runs the jobs it sends.
type Agent struct {
	Cfg    *Config
	Creds  *Credentials
	Runner runner.Runner
	Log    func(string)

	url        string
	audit      *AuditLog
	seen       *SeenJobs
	spool      *spool.Spool
	beatFile   string
	metrics    *host.Metrics
	slots      chan struct{}
	writeLock  sync.Mutex
	ctx        context.Context
	cancel     context.CancelFunc
	mu         sync.Mutex // guards conn, bundles, pendingKey, heartbeat, lastContact
	sendMu     sync.Mutex
	conn       *websocket.Conn
	bundles    map[string]chan bundleAnswer
	pendingKey *ecdsa.PrivateKey
	heartbeat  time.Duration
	beatReset  chan struct{} // the hub set another heartbeat interval
	lastWrite  time.Time
}

type bundleAnswer struct {
	data []byte
	err  error
}

// New makes an agent from its config and credentials.
func New(cfg *Config, creds *Credentials, r runner.Runner, log func(string)) (*Agent, error) {
	if err := os.MkdirAll(cfg.StateDir, 0o700); err != nil {
		return nil, err
	}
	sp, err := spool.Open(filepath.Join(cfg.StateDir, "buffer"), log)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	url := creds.URL
	if url == "" {
		url = cfg.URL
	}
	return &Agent{Cfg: cfg, Creds: creds, Runner: r, Log: log, url: strings.TrimRight(url, "/"),
		audit: &AuditLog{path: filepath.Join(cfg.StateDir, "audit.log")}, seen: OpenSeen(filepath.Join(cfg.StateDir, "seen.json")),
		spool: sp, beatFile: filepath.Join(cfg.StateDir, "heartbeat"), metrics: host.NewMetrics(host.Proc),
		slots: make(chan struct{}, Workers), ctx: ctx, cancel: cancel, bundles: map[string]chan bundleAnswer{},
		heartbeat: protocol.Heartbeat, beatReset: make(chan struct{}, 1)}, nil
}

// -- sending

func (a *Agent) send(message map[string]any) bool {
	a.mu.Lock()
	c := a.conn
	a.mu.Unlock()
	if c == nil {
		return false
	}
	b, err := json.Marshal(message)
	if err != nil {
		return false
	}
	ctx, cancel := context.WithTimeout(a.ctx, 30*time.Second)
	defer cancel()
	a.sendMu.Lock()
	defer a.sendMu.Unlock()
	return c.Write(ctx, websocket.MessageText, b) == nil // a dropped link: the caller buffers it
}

func (a *Agent) sendOrBuffer(message map[string]any) {
	if !a.send(message) {
		rec := map[string]any{}
		for k, v := range message {
			rec[k] = v
		}
		rec["buffered"] = true
		a.spool.Put([]map[string]any{rec})
	}
}

func (a *Agent) replay() int {
	sent := 0
	for a.ctx.Err() == nil {
		name, records, ok := a.spool.Oldest()
		if !ok {
			break
		}
		for _, r := range records {
			if !a.send(r) {
				return sent
			}
		}
		a.spool.Done(name, false)
		sent += len(records)
	}
	return sent
}

// -- the link

func (a *Agent) hello() map[string]any {
	hostname, _ := os.Hostname()
	sha, _ := CertificateInfo(a.Cfg.StateDir)
	return map[string]any{"t": "hello", "version": Version, "protocol": protocol.Protocol, "hostname": hostname,
		"root": a.Runner.Root(), "allow": a.Cfg.AllowList(), "jobs": jobs.Names(), "facts": host.Facts(),
		"buffered": a.spool.Len(), "certs": true, "cert_sha256": sha}
}

// linkURL is the hub URL with https → wss (http → ws in tests).
func (a *Agent) linkURL() string {
	return "ws" + strings.TrimPrefix(a.url, "http") + "/api/agents/link"
}

// link opens one link, says hello, then takes messages until it closes.
func (a *Agent) link() error {
	client, err := a.Cfg.HTTPClient(0)
	if err != nil {
		return err
	}
	header := http.Header{}
	header.Set(protocol.KeyHeader, a.Creds.Key)
	header.Set(protocol.VersionHeader, Version)
	header.Set(protocol.ProtocolHeader, strconv.Itoa(protocol.Protocol))
	dctx, cancel := context.WithTimeout(a.ctx, ConnectTimeout)
	c, resp, err := websocket.Dial(dctx, a.linkURL(), &websocket.DialOptions{HTTPClient: client, HTTPHeader: header})
	cancel()
	if err != nil {
		if resp != nil && (resp.StatusCode == 401 || resp.StatusCode == 403 || resp.StatusCode == 426) {
			return &Refused{fmt.Sprintf("the hub refused this agent (%d): enrol it again", resp.StatusCode)}
		}
		return err
	}
	c.SetReadLimit(protocol.MaxMessage)
	a.mu.Lock()
	a.conn = c
	a.mu.Unlock()
	pctx, stopPing := context.WithCancel(a.ctx)
	defer func() {
		stopPing()
		a.mu.Lock()
		a.conn = nil
		a.mu.Unlock()
		_ = c.CloseNow()
	}()
	go a.pinger(pctx, c)
	a.send(a.hello())
	for a.ctx.Err() == nil {
		rctx, cancel := context.WithTimeout(a.ctx, LinkIdle)
		typ, raw, err := c.Read(rctx)
		idle := errors.Is(rctx.Err(), context.DeadlineExceeded)
		cancel()
		if err != nil {
			if a.ctx.Err() != nil {
				return nil
			}
			if idle {
				return fmt.Errorf("the link was quiet for %.0f s", LinkIdle.Seconds())
			}
			var ce websocket.CloseError
			if errors.As(err, &ce) {
				code := int(ce.Code)
				if code == protocol.CloseCert {
					return fmt.Errorf("the hub wants a valid client certificate: %s", ce.Reason)
				}
				if protocol.Fatal(code) {
					return &Refused{fmt.Sprintf("the hub closed the link for good (%d: %s)", code, ce.Reason)}
				}
				return fmt.Errorf("the hub closed the link (%d)", code)
			}
			return err
		}
		a.contact()
		if typ != websocket.MessageText {
			continue
		}
		var message map[string]any
		if json.Unmarshal(raw, &message) != nil || message == nil {
			continue
		}
		a.handle(message)
	}
	return nil
}

func (a *Agent) pinger(ctx context.Context, c *websocket.Conn) {
	t := time.NewTicker(PingEvery)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			pctx, cancel := context.WithTimeout(ctx, PingEvery)
			_ = c.Ping(pctx) // a dead link shows up in the read loop's idle timeout
			cancel()
		}
	}
}

func (a *Agent) handle(message map[string]any) {
	switch message["t"] {
	case "welcome":
		hb := protocol.Heartbeat
		if v, ok := message["heartbeat"].(float64); ok && v != 0 {
			hb = time.Duration(math.Max(1, math.Min(float64(int64(v)), 300))) * time.Second
		}
		a.mu.Lock()
		changed := a.heartbeat != hb
		a.heartbeat = hb
		a.mu.Unlock()
		if changed {
			select {
			case a.beatReset <- struct{}{}:
			default:
			}
		}
		a.Log(fmt.Sprintf("[agent] linked to %s as %v", a.url, message["host"]))
		if n := a.replay(); n > 0 {
			a.Log(fmt.Sprintf("[agent] sent %d buffered record(s)", n))
		}
	case "job":
		a.take(message)
	case "renew":
		key, csr, err := NewRequest()
		if err != nil {
			a.Log("[agent] couldn't make a certificate request: " + err.Error())
			return
		}
		a.mu.Lock()
		a.pendingKey = key
		a.mu.Unlock()
		a.send(map[string]any{"t": "csr", "csr": csr})
	case "cert":
		a.mu.Lock()
		key := a.pendingKey
		a.pendingKey = nil
		a.mu.Unlock()
		if e := message["error"]; protocol.Truthy(e) || key == nil {
			why := "none was asked for"
			if protocol.Truthy(e) {
				why = fmt.Sprint(e)
			}
			a.Log("[agent] no new client certificate: " + why)
			return
		}
		cert, _ := message["cert"].(string)
		if err := SaveCertificate(a.Cfg.StateDir, key, cert); err != nil {
			a.Log("[agent] couldn't save the new client certificate: " + err.Error())
			return
		}
		a.Log("[agent] saved a new client certificate; it is used from the next link")
	case "bundle":
		sha := fmt.Sprint(message["sha256"])
		a.mu.Lock()
		ch := a.bundles[sha]
		a.mu.Unlock()
		if ch == nil {
			return
		}
		var ans bundleAnswer
		if e := message["error"]; protocol.Truthy(e) {
			ans.err = protocol.Bad("%v", e)
		} else {
			data, _ := message["data"].(string)
			b, err := protocol.UnB64(data)
			if err != nil {
				ans.err = protocol.Bad("bad bundle data: %v", err)
			}
			ans.data = b
		}
		select {
		case ch <- ans:
		default: // answered already
		}
	}
}

func (a *Agent) contact() {
	now := time.Now()
	a.mu.Lock()
	write := now.Sub(a.lastWrite) >= 10*time.Second
	if write {
		a.lastWrite = now
	}
	a.mu.Unlock()
	if write {
		_ = os.WriteFile(a.beatFile, []byte(strconv.FormatInt(now.Unix(), 10)+"\n"), 0o644)
	}
}

// -- jobs

func (a *Agent) take(message map[string]any) {
	env, err := protocol.Verify(a.Creds.SigningKey, message, a.Creds.AgentID, time.Now())
	if err != nil {
		a.audit.Write("refused", "reason", err.Error())
		if id := protocol.JobID(message); id != "" {
			a.sendOrBuffer(map[string]any{"t": "result", "id": id, "ok": false, "refused": true, "error": err.Error()})
		}
		return
	}
	var missing []string
	for _, c := range protocol.CapabilitiesFor(env.Type, env.Params) {
		if !a.Cfg.Allow[c] {
			missing = append(missing, c)
		}
	}
	if len(missing) > 0 {
		a.refuse(env, "capability not switched on in this host's agent.conf: "+strings.Join(missing, ", "), true)
		return
	}
	if !a.seen.Add(env.ID, env.Expires, time.Now()) {
		a.refuse(env, "job already run (replay)", false)
		return
	}
	a.audit.Write("accepted", "id", env.ID, "type", env.Type, "params", env.Params, "by", env.By)
	a.send(map[string]any{"t": "ack", "id": env.ID})
	go func() {
		select {
		case a.slots <- struct{}{}:
		case <-a.ctx.Done():
			return
		}
		defer func() { <-a.slots }()
		a.run(env)
	}()
}

func (a *Agent) refuse(env *protocol.Envelope, why string, answer bool) {
	a.audit.Write("refused", "id", env.ID, "type", env.Type, "reason", why)
	if answer {
		a.sendOrBuffer(map[string]any{"t": "result", "id": env.ID, "ok": false, "refused": true, "error": why})
	}
}

func (a *Agent) run(env *protocol.Envelope) {
	started := time.Now()
	ctx := &jobs.Context{Runner: a.Runner, Allow: a.Cfg.Allow, StateDir: a.Cfg.StateDir, FetchBundle: a.fetchBundle, WriteLock: &a.writeLock}
	var result map[string]any
	ok, errText := true, ""
	func() {
		defer func() {
			if r := recover(); r != nil { // a failing job is a result, not a dead agent
				result, ok, errText = nil, false, fmt.Sprintf("panic: %v", r)
			}
		}()
		res, err := jobs.Handlers[env.Type](env.Params, ctx)
		var bad *protocol.BadJob
		switch {
		case errors.As(err, &bad):
			result, ok, errText = nil, false, bad.Reason
		case err != nil:
			result, ok, errText = nil, false, err.Error()
		default:
			result = res
			if v, has := res["ok"]; has {
				ok = protocol.Truthy(v)
			}
			if e, has := res["error"]; has && protocol.Truthy(e) {
				errText = fmt.Sprint(e)
			}
		}
	}()
	finished := time.Now()
	short := []rune(errText)
	if len(short) > 300 {
		short = short[:300]
	}
	a.audit.Write("finished", "id", env.ID, "type", env.Type, "ok", ok, "error", string(short),
		"seconds", math.Round(finished.Sub(started).Seconds()*100)/100)
	var res any
	if result != nil {
		res = result
	}
	a.sendOrBuffer(map[string]any{"t": "result", "id": env.ID, "ok": ok, "result": res, "error": errText,
		"started": unix(started), "finished": unix(finished)})
}

func unix(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

func (a *Agent) fetchBundle(sha string) ([]byte, error) {
	ch := make(chan bundleAnswer, 1)
	a.mu.Lock()
	a.bundles[sha] = ch
	a.mu.Unlock()
	defer func() {
		a.mu.Lock()
		delete(a.bundles, sha)
		a.mu.Unlock()
	}()
	if !a.send(map[string]any{"t": "bundle.get", "sha256": sha}) {
		return nil, protocol.Bad("the link is down: can't fetch the bundle")
	}
	select {
	case ans := <-ch:
		return ans.data, ans.err
	case <-time.After(BundleWait):
		return nil, protocol.Bad("the hub didn't send the bundle in time")
	case <-a.ctx.Done():
		return nil, protocol.Bad("the agent is stopping")
	}
}

// -- heartbeats

func (a *Agent) beatLoop() {
	for {
		a.mu.Lock()
		every := a.heartbeat
		a.mu.Unlock()
		select {
		case <-a.ctx.Done():
			return
		case <-a.beatReset:
			continue // wait the new interval from now
		case <-time.After(every):
		}
		a.sendOrBuffer(map[string]any{"t": "heartbeat", "ts": unix(time.Now()), "metrics": a.metrics.Collect()})
	}
}

// -- running

// Stop ends Run.
func (a *Agent) Stop() {
	a.cancel()
	a.mu.Lock()
	c := a.conn
	a.mu.Unlock()
	if c != nil {
		_ = c.Close(websocket.StatusNormalClosure, "")
	}
}

// Run links until stopped and reconnects with backoff. It returns a *Refused when the hub
// won't take this agent, nil when stopped.
func (a *Agent) Run() error {
	go a.beatLoop()
	backoff := time.Second
	for a.ctx.Err() == nil {
		started := time.Now()
		err := a.link()
		var refused *Refused
		if errors.As(err, &refused) {
			return err
		}
		if a.ctx.Err() != nil {
			break
		}
		if time.Since(started) > 60*time.Second {
			backoff = time.Second // it was up a while: a fresh drop, not a hub that keeps refusing
		}
		why := "closed"
		if err != nil {
			why = err.Error()
		}
		a.Log(fmt.Sprintf("[agent] no link to %s (%s); trying again in %.0f s", a.url, why, backoff.Seconds()))
		select {
		case <-a.ctx.Done():
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, MaxBackoff)
	}
	return nil
}

// Health is whether the hub answered within HealthyFor, and why.
func Health(stateDir string, now time.Time) (bool, string) {
	b, err := os.ReadFile(filepath.Join(stateDir, "heartbeat"))
	if err != nil {
		return false, "the hub hasn't answered yet"
	}
	t, err := strconv.ParseFloat(strings.TrimSpace(string(b)), 64)
	if err != nil {
		return false, "the hub hasn't answered yet"
	}
	age := float64(now.UnixNano())/1e9 - t
	if age > HealthyFor.Seconds() {
		return false, fmt.Sprintf("the hub last answered %.0f s ago", age)
	}
	return true, fmt.Sprintf("the hub answered %.0f s ago", age)
}
