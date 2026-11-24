package agent

import (
	"crypto/ed25519"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/labdex-app/labdex/agent/internal/protocol"
	"github.com/labdex-app/labdex/agent/internal/runner"
)

func TestLoadConfig(t *testing.T) {
	p := filepath.Join(t.TempDir(), "agent.conf")
	_ = os.WriteFile(p, []byte("# x\n[other]\nurl = https://wrong\n[agent]\nURL = https://hub.example.com/\nallow = patch, reboot\n; c\nca_file: /ca.pem\n"), 0o644)
	c, err := LoadConfig(p, "")
	if err != nil || c.URL != "https://hub.example.com" || !c.Allow["patch"] || !c.Allow["reboot"] || c.CAFile != "/ca.pem" || c.StateDir != StateDir {
		t.Fatalf("%+v %v", c, err)
	}
	if c, _ := LoadConfig(p, "https://other.example.com"); c.URL != "https://other.example.com" {
		t.Fatal("--url overrides the file")
	}
	_ = os.WriteFile(p, []byte("[agent]\nallow = patch shell root\n"), 0o644)
	if _, err := LoadConfig(p, ""); err == nil || !strings.Contains(err.Error(), "unknown capabilities in allow: root, shell") {
		t.Fatal(err)
	}
}

func TestSeenJobs(t *testing.T) {
	p := filepath.Join(t.TempDir(), "seen.json")
	now := time.Unix(1764000000, 0)
	s := OpenSeen(p)
	if !s.Add("a", 1764000600, now) || s.Add("a", 1764000600, now) {
		t.Fatal("a second time is a replay")
	}
	if OpenSeen(p).Add("a", 1764000600, now) {
		t.Fatal("kept across restarts")
	}
	// gone once expired past the skew
	if !OpenSeen(p).Add("a", 1764000600, now.Add(901*time.Second)) {
		t.Fatal("an expired id is forgotten")
	}
}

type fakeRunner struct{}

func (fakeRunner) Root() bool                                             { return false }
func (fakeRunner) Has(string) bool                                        { return false }
func (fakeRunner) Run([]string, time.Duration) (int, string, string)      { return 127, "", "" }
func (fakeRunner) Privileged(string, time.Duration) (int, string, string) { return 127, "", "" }

var _ runner.Runner = fakeRunner{}

func signed(t *testing.T, key ed25519.PrivateKey, env map[string]any) map[string]any {
	b, _ := json.Marshal(env)
	return map[string]any{"t": "job", "job": string(b), "sig": protocol.B64(ed25519.Sign(key, b))}
}

// Offline, every answer goes to the buffer: refusals and the result of a job that ran.
func TestTakeChecksAndBuffers(t *testing.T) {
	seed := make([]byte, 32)
	for i := range seed {
		seed[i] = byte(i)
	}
	key := ed25519.NewKeyFromSeed(seed)
	dir := t.TempDir()
	cfg := &Config{Allow: map[string]bool{}, StateDir: dir}
	creds := &Credentials{AgentID: "1a2b3c4d", SigningKey: protocol.B64(key.Public().(ed25519.PublicKey)), URL: "https://hub.example.com"}
	a, err := New(cfg, creds, fakeRunner{}, func(string) {})
	if err != nil {
		t.Fatal(err)
	}
	now := float64(time.Now().Unix())
	env := func(id, typ string, params map[string]any) map[string]any {
		return map[string]any{"v": 1, "id": id, "agent": "1a2b3c4d", "host": "h", "type": typ, "params": params,
			"issued": now, "expires": now + 600, "by": "admin"}
	}
	a.take(signed(t, key, env("j1", "ping", map[string]any{"echo": "hi"})))
	a.take(signed(t, key, env("j1", "ping", map[string]any{}))) // replay: no answer
	a.take(signed(t, key, env("j2", "patch.apply", map[string]any{"reboot": true})))
	bad := signed(t, key, env("j3", "ping", map[string]any{}))
	bad["sig"] = protocol.B64(make([]byte, 64))
	a.take(bad)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if b, _ := os.ReadFile(filepath.Join(dir, "audit.log")); strings.Contains(string(b), `"event":"finished"`) {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	var results []map[string]any
	for {
		name, recs, ok := a.spool.Oldest()
		if !ok {
			break
		}
		results = append(results, recs...)
		a.spool.Done(name, false)
	}
	got := map[string]string{}
	for _, r := range results {
		if r["buffered"] != true {
			t.Errorf("not marked buffered: %v", r)
		}
		got[r["id"].(string)] += describe(r)
	}
	if got["j1"] != "ok" || got["j2"] != "refused:capability not switched on in this host's agent.conf: patch, reboot" ||
		got["j3"] != "refused:bad signature" || len(got) != 3 {
		t.Fatalf("results: %v", got)
	}
	audit, _ := os.ReadFile(filepath.Join(dir, "audit.log"))
	text := string(audit)
	for _, want := range []string{`"event":"accepted","id":"j1"`, `"reason":"job already run (replay)"`,
		`"reason":"capability not switched on in this host's agent.conf: patch, reboot"`, `"reason":"bad signature"`, `"event":"finished","id":"j1"`} {
		if !strings.Contains(text, want) {
			t.Errorf("audit.log lacks %s:\n%s", want, text)
		}
	}
}

func describe(r map[string]any) string {
	if r["refused"] == true {
		return "refused:" + r["error"].(string)
	}
	if r["ok"] == true {
		return "ok"
	}
	return "failed:" + r["error"].(string)
}
