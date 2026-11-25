// Package protocol is what the agent and the hub agree on: headers, limits, close codes, the job
// allowlist and job signing. The contract is docs/develop/agent-protocol.md.
package protocol

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

const (
	Protocol       = 1
	KeyHeader      = "Labdex-Agent-Key"
	VersionHeader  = "Labdex-Agent-Version"
	ProtocolHeader = "Labdex-Agent-Protocol"

	Heartbeat  = 30 * time.Second
	MaxSkew    = 300 // seconds a job's times may be off between hub and host clocks
	MaxMessage = 16 * 1024 * 1024

	CloseRefused  = 4401 // unknown or revoked key
	CloseRoot     = 4403 // runs as root, and the host isn't allowed a root agent
	CloseProtocol = 4426 // one side has to be updated
	CloseCert     = 4495 // no client certificate, or the wrong one: try again later
)

// Fatal reports whether a close code means: stop for good, don't reconnect.
func Fatal(code int) bool {
	return code == CloseRefused || code == CloseRoot || code == CloseProtocol
}

// JobType is one entry of the allowlist. Write is "" for read-only jobs.
type JobType struct {
	Write   string
	Timeout int
	About   string
}

// JobTypes is every job there is. Nothing else runs, and no job takes a command line.
var JobTypes = map[string]JobType{
	"ping":               {"", 10, "answer, to test the link"},
	"facts":              {"", 30, "OS, kernel, CPU, memory, disks"},
	"discovery.services": {"", 120, "listening ports and the processes behind them, running systemd units, running containers"},
	"patch.report":       {"", 600, "updates waiting and whether a reboot is needed"},
	"patch.apply":        {"patch", 3600, "install the waiting updates (a reboot needs the reboot capability too)"},
	"ansible.run":        {"ansible", 3600, "run a signed playbook bundle from git on this host"},
	// Go agent only (the hub sends it to agents whose hello lists it): labdex runs Ansible itself
	// and reaches this host's sshd through pipes in the link. See "Ansible over the link".
	"ansible.session": {"ansible", 3600, "let labdex reach this host's sshd through the link for one Ansible run"},
	// Go agent only: SSH login keys. labdex's keys go into /etc/ssh/labdex, never into a user's
	// own authorized_keys, and only for the users agent.conf names in ssh_users.
	"ssh_keys.report": {"", 120, "which public keys may log in as which user, and the sshd settings that decide it"},
	"ssh_keys.apply":  {"ssh_keys", 300, "write labdex's SSH keys (and the break-glass key) for the users in ssh_users"},
}

// GoOnly are the job types only the Go agent has; the contract's shared cases leave them out.
var GoOnly = map[string]bool{"ansible.session": true, "ssh_keys.report": true, "ssh_keys.apply": true}

// Pipes: byte streams in the link from the hub to this host's sshd, for ansible.session.
const (
	MaxSessionMinutes  = 60        // an Ansible session lasts at most this long
	MaxPipesPerSession = 4         // ssh connections at once in one session
	MaxPipes           = 8         // in all sessions together
	PipeChunk          = 32 * 1024 // bytes per pipe.data message, before base64
	PipeQueue          = 256       // pipe.data messages from the hub waiting for sshd, per pipe
)

// Capabilities are the write capabilities, each switched on at both ends.
var Capabilities = []string{"patch", "reboot", "ansible", "ssh_keys"}

// IsCapability reports whether c is a known write capability.
func IsCapability(c string) bool {
	for _, k := range Capabilities {
		if k == c {
			return true
		}
	}
	return false
}

// BadJob is a job the agent won't run; the text says why and goes to both audit logs.
type BadJob struct{ Reason string }

func (e *BadJob) Error() string { return e.Reason }

// Bad makes a BadJob.
func Bad(format string, a ...any) error { return &BadJob{fmt.Sprintf(format, a...)} }

// CapabilitiesFor returns the sorted write capabilities a job needs.
func CapabilitiesFor(jobType string, params map[string]any) []string {
	need := map[string]bool{}
	if jt, ok := JobTypes[jobType]; ok && jt.Write != "" {
		need[jt.Write] = true
	}
	if jobType == "patch.apply" && Truthy(params["reboot"]) {
		need["reboot"] = true
	}
	out := make([]string, 0, len(need))
	for k := range need {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// Truthy is Python's truth value for a decoded JSON value.
func Truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case float64:
		return x != 0
	case json.Number:
		f, err := x.Float64()
		return err == nil && f != 0
	case string:
		return x != ""
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

// Envelope is a job that passed every check.
type Envelope struct {
	ID      string
	Agent   string
	Host    string
	Type    string
	Params  map[string]any
	Issued  float64
	Expires float64
	By      string
	Raw     map[string]any // as decoded, for the conformance tests
}

// Verify checks a job message on the agent side, in the order the spec gives, and returns its
// envelope. The signature covers the exact text, so JSON serialisation never has to match.
func Verify(publicKeyB64 string, message map[string]any, agentID string, now time.Time) (*Envelope, error) {
	text, ok1 := message["job"].(string)
	sig, ok2 := message["sig"].(string)
	if !ok1 || !ok2 {
		return nil, Bad("not a signed job")
	}
	pub, err := base64.StdEncoding.Strict().DecodeString(publicKeyB64)
	if err != nil || len(pub) != ed25519.PublicKeySize {
		return nil, Bad("bad signature")
	}
	sigRaw, err := base64.StdEncoding.Strict().DecodeString(sig)
	if err != nil || !ed25519.Verify(ed25519.PublicKey(pub), []byte(text), sigRaw) {
		return nil, Bad("bad signature")
	}
	var decoded any
	if err := json.Unmarshal([]byte(text), &decoded); err != nil {
		return nil, Bad("unknown job format")
	}
	env, ok := decoded.(map[string]any)
	if !ok {
		return nil, Bad("unknown job format")
	}
	if v, ok := env["v"].(float64); !ok || v != 1 {
		return nil, Bad("unknown job format")
	}
	if a, _ := env["agent"].(string); a != agentID {
		return nil, Bad("addressed to another agent")
	}
	issued, ok1 := env["issued"].(float64)
	expires, ok2 := env["expires"].(float64)
	if !ok1 || !ok2 {
		return nil, Bad("job without times")
	}
	t := float64(now.UnixNano()) / 1e9
	if issued > t+MaxSkew {
		return nil, Bad("issued in the future: check the clocks")
	}
	if expires < t-MaxSkew {
		return nil, Bad("expired")
	}
	jobType, isStr := env["type"].(string)
	if _, known := JobTypes[jobType]; !isStr || !known {
		return nil, Bad("unknown job type %s", PyRepr(env["type"]))
	}
	params, ok1 := env["params"].(map[string]any)
	id, ok2 := env["id"].(string)
	if !ok1 || !ok2 {
		return nil, Bad("job without id or params")
	}
	host, _ := env["host"].(string)
	by, _ := env["by"].(string)
	return &Envelope{ID: id, Agent: agentID, Host: host, Type: jobType, Params: params,
		Issued: issued, Expires: expires, By: by, Raw: env}, nil
}

// JobID is the id inside a job message that failed its checks, "" when there is none.
func JobID(message map[string]any) string {
	text, _ := message["job"].(string)
	var env map[string]any
	if json.Unmarshal([]byte(text), &env) != nil {
		return ""
	}
	id, _ := env["id"].(string)
	return id
}

// PyRepr writes a value the way Python's repr() does for the simple cases that reach error texts,
// so both agents put the same words in the audit logs.
func PyRepr(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case bool:
		if x {
			return "True"
		}
		return "False"
	case string:
		if strings.Contains(x, "'") && !strings.Contains(x, `"`) {
			return `"` + x + `"`
		}
		return "'" + strings.ReplaceAll(strings.ReplaceAll(x, `\`, `\\`), "'", `\'`) + "'"
	case float64:
		if x == float64(int64(x)) {
			return fmt.Sprintf("%d", int64(x))
		}
		return fmt.Sprintf("%v", x)
	}
	b, _ := json.Marshal(v)
	return string(b)
}

// B64 is standard base64 with padding, as both ends use it.
func B64(b []byte) string { return base64.StdEncoding.EncodeToString(b) }

// UnB64 decodes standard base64 strictly.
func UnB64(s string) ([]byte, error) { return base64.StdEncoding.Strict().DecodeString(s) }
