package agent

import (
	"fmt"
	"net"
	"regexp"
	"strconv"
	"sync"
	"time"

	"github.com/labdex-app/labdex/agent/internal/protocol"
)

// Pipes carry an Ansible run's SSH connections from the hub to this host's sshd: labdex keeps
// the keys and runs ansible-playbook, the agent only moves bytes. A pipe opens only inside a
// session that a signed ansible.session job started, only to 127.0.0.1:ssh_port, and closes when
// the session ends, its time is up or the link drops.

var pipeID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

type session struct {
	until time.Time
	pipes int
}

type pipe struct {
	id, session string
	conn        net.Conn
	in          chan []byte
	done        chan struct{}
	once        sync.Once
	mu          sync.Mutex
	up, down    int64 // bytes to sshd, bytes from sshd
}

type pipes struct {
	mu       sync.Mutex
	sessions map[string]*session
	open     map[string]*pipe
}

func newPipes() *pipes {
	return &pipes{sessions: map[string]*session{}, open: map[string]*pipe{}}
}

// openSession is jobs.Context.OpenSession.
func (a *Agent) openSession(id string, until time.Time) error {
	a.pipes.mu.Lock()
	if _, ok := a.pipes.sessions[id]; ok {
		a.pipes.mu.Unlock()
		return protocol.Bad("session %s is open already", id)
	}
	a.pipes.sessions[id] = &session{until: until}
	a.pipes.mu.Unlock()
	a.audit.Write("session.open", "session", id, "until", until.Unix(), "port", a.Cfg.SSHPort)
	return nil
}

func (a *Agent) handlePipeOpen(message map[string]any) {
	id, _ := message["id"].(string)
	sid, _ := message["session"].(string)
	if !pipeID.MatchString(id) {
		return // nothing to answer to
	}
	refuse := func(why string) {
		a.audit.Write("pipe.refused", "id", id, "session", sid, "reason", why)
		a.send(map[string]any{"t": "pipe.close", "id": id, "error": why})
	}
	a.pipes.mu.Lock()
	s := a.pipes.sessions[sid]
	why := ""
	switch {
	case s == nil:
		why = "no such session"
	case !time.Now().Before(s.until):
		why = "the session is over"
	case a.pipes.open[id] != nil:
		why = "a pipe with this id is open already"
	case s.pipes >= protocol.MaxPipesPerSession:
		why = fmt.Sprintf("the session has %d pipes open already", protocol.MaxPipesPerSession)
	case len(a.pipes.open) >= protocol.MaxPipes:
		why = fmt.Sprintf("%d pipes are open already", protocol.MaxPipes)
	}
	if why != "" {
		a.pipes.mu.Unlock()
		refuse(why)
		return
	}
	p := &pipe{id: id, session: sid, in: make(chan []byte, protocol.PipeQueue), done: make(chan struct{})}
	s.pipes++
	a.pipes.open[id] = p // held while dialling, so the limits count it
	a.pipes.mu.Unlock()

	// the address is fixed here: nothing in a message can point a pipe anywhere else
	conn, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(a.Cfg.SSHPort)), 5*time.Second)
	if err != nil {
		a.forget(p)
		refuse(fmt.Sprintf("can't reach sshd on 127.0.0.1:%d: %v", a.Cfg.SSHPort, err))
		return
	}
	p.conn = conn
	a.audit.Write("pipe.open", "id", id, "session", sid)
	if !a.send(map[string]any{"t": "pipe.opened", "id": id}) {
		a.closePipe(p, "the link is down", false)
		return
	}
	go a.pipeWriter(p)
	go a.pipeReader(p)
}

func (a *Agent) handlePipeData(message map[string]any) {
	p := a.pipe(message)
	if p == nil {
		return
	}
	s, _ := message["data"].(string)
	b, err := protocol.UnB64(s)
	if err != nil || len(b) > protocol.PipeChunk {
		a.closePipe(p, "bad pipe.data", true)
		return
	}
	select {
	case p.in <- b:
	case <-p.done:
	default: // never block the link's read loop on a slow sshd
		a.closePipe(p, "too much data waiting for sshd", true)
	}
}

func (a *Agent) handlePipeClose(message map[string]any) {
	if p := a.pipe(message); p != nil {
		a.closePipe(p, "", false)
	}
}

// handleSessionEnd: the hub's run is over; close its pipes and forget the session.
func (a *Agent) handleSessionEnd(message map[string]any) {
	sid, _ := message["session"].(string)
	a.endSession(sid, "the run is over")
}

func (a *Agent) endSession(sid, why string) {
	a.pipes.mu.Lock()
	_, ok := a.pipes.sessions[sid]
	delete(a.pipes.sessions, sid)
	var open []*pipe
	for _, p := range a.pipes.open {
		if p.session == sid {
			open = append(open, p)
		}
	}
	a.pipes.mu.Unlock()
	for _, p := range open {
		a.closePipe(p, why, true)
	}
	if ok {
		a.audit.Write("session.end", "session", sid, "reason", why)
	}
}

func (a *Agent) pipe(message map[string]any) *pipe {
	id, _ := message["id"].(string)
	a.pipes.mu.Lock()
	defer a.pipes.mu.Unlock()
	p := a.pipes.open[id]
	if p == nil || p.conn == nil {
		return nil
	}
	return p
}

func (a *Agent) pipeWriter(p *pipe) {
	for {
		select {
		case <-p.done:
			return
		case b := <-p.in:
			if _, err := p.conn.Write(b); err != nil {
				a.closePipe(p, "sshd closed the connection", true)
				return
			}
			p.mu.Lock()
			p.up += int64(len(b))
			p.mu.Unlock()
		}
	}
}

func (a *Agent) pipeReader(p *pipe) {
	buf := make([]byte, protocol.PipeChunk)
	for {
		n, err := p.conn.Read(buf)
		if n > 0 {
			p.mu.Lock()
			p.down += int64(n)
			p.mu.Unlock()
			// never buffered: a pipe is live or gone
			if !a.send(map[string]any{"t": "pipe.data", "id": p.id, "data": protocol.B64(buf[:n])}) {
				a.closePipe(p, "the link is down", false)
				return
			}
		}
		if err != nil {
			a.closePipe(p, "", true) // sshd hung up: the normal end
			return
		}
	}
}

// forget drops a pipe from the books (and its session's count).
func (a *Agent) forget(p *pipe) {
	a.pipes.mu.Lock()
	defer a.pipes.mu.Unlock()
	if a.pipes.open[p.id] == p {
		delete(a.pipes.open, p.id)
		if s := a.pipes.sessions[p.session]; s != nil {
			s.pipes--
		}
	}
}

// closePipe closes a pipe once; tell sends pipe.close to the hub.
func (a *Agent) closePipe(p *pipe, why string, tell bool) {
	p.once.Do(func() {
		close(p.done)
		if p.conn != nil {
			_ = p.conn.Close()
		}
		a.forget(p)
		p.mu.Lock()
		up, down := p.up, p.down
		p.mu.Unlock()
		a.audit.Write("pipe.close", "id", p.id, "session", p.session, "up", up, "down", down, "reason", why)
		if tell {
			m := map[string]any{"t": "pipe.close", "id": p.id}
			if why != "" {
				m["error"] = why
			}
			a.send(m)
		}
	})
}

// closeAllPipes: the link dropped. Sessions stay until their time is up.
func (a *Agent) closeAllPipes(why string) {
	a.pipes.mu.Lock()
	var open []*pipe
	for _, p := range a.pipes.open {
		open = append(open, p)
	}
	a.pipes.mu.Unlock()
	for _, p := range open {
		a.closePipe(p, why, false)
	}
}

// expire ends sessions whose time is up.
func (a *Agent) expire(now time.Time) {
	a.pipes.mu.Lock()
	var over []string
	for id, s := range a.pipes.sessions {
		if !now.Before(s.until) {
			over = append(over, id)
		}
	}
	a.pipes.mu.Unlock()
	for _, id := range over {
		a.endSession(id, "the session's time is up")
	}
}

func (a *Agent) expireLoop() {
	t := time.NewTicker(5 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-a.ctx.Done():
			a.closeAllPipes("the agent is stopping")
			return
		case now := <-t.C:
			a.expire(now)
		}
	}
}
