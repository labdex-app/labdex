package agent

import (
	"io"
	"net"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/labdex-app/labdex/agent/internal/protocol"
)

// pipeAgent is an agent whose link is a channel, with an echo server standing in for sshd.
func pipeAgent(t *testing.T) (*Agent, chan map[string]any) {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = l.Close() })
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			go func() { _, _ = io.Copy(c, c); _ = c.Close() }()
		}
	}()
	dir := t.TempDir()
	cfg := &Config{URL: "https://hub.example.com", Allow: map[string]bool{"ansible": true}, StateDir: dir,
		SSHPort: l.Addr().(*net.TCPAddr).Port}
	a, err := New(cfg, &Credentials{AgentID: "1a2b3c4d"}, fakeRunner{}, func(string) {})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Stop)
	out := make(chan map[string]any, 64)
	var mu sync.Mutex
	a.sendHook = func(m map[string]any) bool {
		mu.Lock()
		defer mu.Unlock()
		out <- m
		return true
	}
	return a, out
}

func next(t *testing.T, out chan map[string]any, kind string) map[string]any {
	t.Helper()
	select {
	case m := <-out:
		if m["t"] != kind {
			t.Fatalf("got %v, want %s", m, kind)
		}
		return m
	case <-time.After(5 * time.Second):
		t.Fatalf("no %s", kind)
	}
	return nil
}

func TestPipeEchoesThroughSSHPort(t *testing.T) {
	a, out := pipeAgent(t)
	if err := a.openSession("run-0001", time.Now().Add(time.Minute)); err != nil {
		t.Fatal(err)
	}
	if err := a.openSession("run-0001", time.Now().Add(time.Minute)); err == nil {
		t.Fatal("a session opens once")
	}
	a.handlePipeOpen(map[string]any{"t": "pipe.open", "id": "p1", "session": "run-0001"})
	next(t, out, "pipe.opened")
	a.handlePipeData(map[string]any{"t": "pipe.data", "id": "p1", "data": protocol.B64([]byte("SSH-2.0-test\r\n"))})
	m := next(t, out, "pipe.data")
	if b, _ := protocol.UnB64(m["data"].(string)); string(b) != "SSH-2.0-test\r\n" {
		t.Fatalf("echo: %q", b)
	}
	a.handleSessionEnd(map[string]any{"t": "session.end", "session": "run-0001"})
	if m := next(t, out, "pipe.close"); m["id"] != "p1" {
		t.Fatal(m)
	}
	// the session is gone: no new pipes in it
	a.handlePipeOpen(map[string]any{"t": "pipe.open", "id": "p2", "session": "run-0001"})
	if m := next(t, out, "pipe.close"); m["error"] != "no such session" {
		t.Fatal(m)
	}
}

func TestPipeRefusals(t *testing.T) {
	a, out := pipeAgent(t)
	_ = a.openSession("run-0002", time.Now().Add(time.Minute))
	_ = a.openSession("run-over", time.Now().Add(-time.Second))
	a.handlePipeOpen(map[string]any{"id": "x", "session": "run-over"})
	if m := next(t, out, "pipe.close"); m["error"] != "the session is over" {
		t.Fatal(m)
	}
	a.handlePipeOpen(map[string]any{"id": "../x", "session": "run-0002"})
	select {
	case m := <-out:
		t.Fatalf("a bad id gets no answer: %v", m)
	case <-time.After(100 * time.Millisecond):
	}
	for i := 0; i < protocol.MaxPipesPerSession; i++ {
		a.handlePipeOpen(map[string]any{"id": "q" + string(rune('a'+i)), "session": "run-0002"})
		next(t, out, "pipe.opened")
	}
	a.handlePipeOpen(map[string]any{"id": "qz", "session": "run-0002"})
	if m := next(t, out, "pipe.close"); !strings.Contains(m["error"].(string), "pipes open already") {
		t.Fatal(m)
	}
	a.handlePipeOpen(map[string]any{"id": "qa", "session": "run-0002"})
	if m := next(t, out, "pipe.close"); m["error"] != "a pipe with this id is open already" {
		t.Fatal(m)
	}
	// the link drops: every pipe closes, without answers (there is no link to answer on)
	a.closeAllPipes("the link closed")
	if n := len(a.pipes.open); n != 0 {
		t.Fatalf("%d pipes left", n)
	}
	if s := a.pipes.sessions["run-0002"]; s == nil || s.pipes != 0 {
		t.Fatalf("the session stays, with no pipes: %+v", s)
	}
}

func TestPipeSessionExpires(t *testing.T) {
	a, out := pipeAgent(t)
	_ = a.openSession("run-0003", time.Now().Add(time.Minute))
	a.handlePipeOpen(map[string]any{"id": "p1", "session": "run-0003"})
	next(t, out, "pipe.opened")
	a.expire(time.Now().Add(2 * time.Minute))
	if m := next(t, out, "pipe.close"); m["error"] != "the session's time is up" {
		t.Fatal(m)
	}
	if len(a.pipes.sessions) != 0 {
		t.Fatal("expired sessions are dropped")
	}
}

func TestPipeNoSSHD(t *testing.T) {
	a, out := pipeAgent(t)
	l, _ := net.Listen("tcp", "127.0.0.1:0")
	a.Cfg.SSHPort = l.Addr().(*net.TCPAddr).Port
	_ = l.Close() // nothing listens there now
	_ = a.openSession("run-0004", time.Now().Add(time.Minute))
	a.handlePipeOpen(map[string]any{"id": "p1", "session": "run-0004"})
	if m := next(t, out, "pipe.close"); !strings.Contains(m["error"].(string), "can't reach sshd on 127.0.0.1:") {
		t.Fatal(m)
	}
	if a.pipes.sessions["run-0004"].pipes != 0 {
		t.Fatal("a failed pipe doesn't count")
	}
}

func TestSSHPortConfig(t *testing.T) {
	p := t.TempDir() + "/agent.conf"
	for conf, want := range map[string]int{"[agent]\n": 22, "[agent]\nssh_port = 2222\n": 2222} {
		_ = writeFile(p, conf)
		c, err := LoadConfig(p, "")
		if err != nil || c.SSHPort != want {
			t.Fatalf("%q: %v %v", conf, c, err)
		}
	}
	for _, bad := range []string{"0", "65536", "ssh"} {
		_ = writeFile(p, "[agent]\nssh_port = "+bad+"\n")
		if _, err := LoadConfig(p, ""); err == nil || !strings.Contains(err.Error(), "ssh_port must be a port number") {
			t.Fatalf("%s: %v", bad, err)
		}
	}
}

func writeFile(p, s string) error { return os.WriteFile(p, []byte(s), 0o644) }
