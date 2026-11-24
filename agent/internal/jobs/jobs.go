// Package jobs holds the job handlers: one per entry of protocol.JobTypes, nothing else.
package jobs

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/labdex-app/labdex/agent/internal/discovery"
	"github.com/labdex-app/labdex/agent/internal/host"
	"github.com/labdex-app/labdex/agent/internal/patch"
	"github.com/labdex-app/labdex/agent/internal/protocol"
	"github.com/labdex-app/labdex/agent/internal/runner"
)

// Context is what a handler gets.
type Context struct {
	Runner   runner.Runner
	Allow    map[string]bool
	StateDir string
	// FetchBundle asks the hub for an Ansible bundle over the link; nil without a link.
	FetchBundle func(sha256 string) ([]byte, error)
	// WriteLock: write jobs run one at a time.
	WriteLock *sync.Mutex
}

// Handler runs one job type. A BadJob error is a refusal; any other error a failure.
type Handler func(params map[string]any, ctx *Context) (map[string]any, error)

// Handlers is every job the agent runs.
var Handlers = map[string]Handler{
	"ping":               ping,
	"facts":              facts,
	"discovery.services": discover,
	"patch.report":       patchReport,
	"patch.apply":        patchApply,
	"ansible.run":        AnsibleRun,
}

// Names is the sorted list of job types this agent runs (for hello).
func Names() []string {
	out := make([]string, 0, len(Handlers))
	for k := range Handlers {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ToMap turns a struct into the JSON object the hub gets.
func ToMap(v any) map[string]any {
	b, _ := json.Marshal(v)
	var out map[string]any
	_ = json.Unmarshal(b, &out)
	return out
}

// pyStr is Python's str() of a decoded JSON value.
func pyStr(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return protocol.PyRepr(v)
}

func head(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

func tail(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[len(r)-n:])
	}
	return s
}

func ping(params map[string]any, _ *Context) (map[string]any, error) {
	echo := ""
	if v, ok := params["echo"]; ok {
		echo = pyStr(v)
	}
	return map[string]any{"pong": true, "time": float64(time.Now().UnixNano()) / 1e9, "echo": head(echo, 200)}, nil
}

func facts(map[string]any, *Context) (map[string]any, error) { return host.Facts(), nil }

func discover(_ map[string]any, ctx *Context) (map[string]any, error) {
	return ToMap(discovery.Collect(ctx.Runner, host.Proc)), nil
}

func patchReport(_ map[string]any, ctx *Context) (map[string]any, error) {
	// never refreshes the lists: that needs root; patch.apply does it
	return patch.Collect(ctx.Runner), nil
}

func unknownParams(params map[string]any, allowed ...string) error {
	ok := map[string]bool{}
	for _, a := range allowed {
		ok[a] = true
	}
	var bad []string
	for k := range params {
		if !ok[k] {
			bad = append(bad, k)
		}
	}
	if len(bad) > 0 {
		sort.Strings(bad)
		return protocol.Bad("unknown parameters: %s", strings.Join(bad, ", "))
	}
	return nil
}

func patchApply(params map[string]any, ctx *Context) (map[string]any, error) {
	if err := unknownParams(params, "reboot"); err != nil {
		return nil, err
	}
	ctx.WriteLock.Lock()
	defer ctx.WriteLock.Unlock()
	var names []string
	switch {
	case ctx.Runner.Has("apt-get"):
		names = []string{"apt_update", "apt_upgrade"}
	case ctx.Runner.Has("dnf"):
		names = []string{"dnf_upgrade"}
	default:
		return nil, protocol.Bad("no apt-get or dnf on this host")
	}
	steps := []map[string]any{}
	for _, name := range names {
		code, out, errText := ctx.Runner.Privileged(name, 3000*time.Second)
		steps = append(steps, map[string]any{"step": name, "code": code, "output": tail(out+errText, 4000)})
		if code != 0 {
			return map[string]any{"ok": false, "steps": steps, "error": fmt.Sprintf("%s failed (%d)", name, code)}, nil
		}
		if name == "apt_update" && patch.AptFetchFailed(out+errText) {
			return map[string]any{"ok": false, "steps": steps, "error": "apt_update couldn't fetch every package list (see its output)"}, nil
		}
	}
	after := patch.Collect(ctx.Runner)
	rebooting := false
	if protocol.Truthy(params["reboot"]) && protocol.Truthy(after["reboot"]) {
		code, out, errText := ctx.Runner.Privileged("reboot", 60*time.Second)
		steps = append(steps, map[string]any{"step": "reboot", "code": code, "output": tail(out+errText, 4000)})
		rebooting = code == 0
	}
	return map[string]any{"ok": true, "steps": steps, "report": after, "rebooting": rebooting}, nil
}
