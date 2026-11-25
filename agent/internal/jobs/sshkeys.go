package jobs

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/labdex-app/labdex/agent/internal/protocol"
	"github.com/labdex-app/labdex/agent/internal/sshkeys"
)

// Both jobs run `labdex-agent ssh-keys …` as root through a fixed sudo line (runner.Privileged):
// the service's sandbox can't see home folders, and only root may write /etc/ssh/labdex.

func sshKeysReport(params map[string]any, ctx *Context) (map[string]any, error) {
	if err := unknownParams(params); err != nil {
		return nil, err
	}
	code, out, errText := ctx.Runner.Privileged("ssh_keys_report", 90*time.Second)
	return rootAnswer("ssh_keys_report", code, out, errText)
}

// DesiredFromParams turns ssh_keys.apply's params into what the root side reads, checked against
// ssh_users: {users: {name: [key, …]}, break_glass?: {user, key}}.
func DesiredFromParams(params map[string]any, users []string) (sshkeys.Desired, error) {
	var d sshkeys.Desired
	if err := unknownParams(params, "users", "break_glass"); err != nil {
		return d, err
	}
	raw, _ := json.Marshal(params)
	if err := json.Unmarshal(raw, &d); err != nil {
		return d, protocol.Bad("users must map user names to lists of keys; break_glass is {user, key}")
	}
	if d.Users == nil {
		return d, protocol.Bad("users is missing")
	}
	if err := sshkeys.Check(d, users); err != nil {
		return d, protocol.Bad("%v", err)
	}
	return d, nil
}

func sshKeysApply(params map[string]any, ctx *Context) (map[string]any, error) {
	d, err := DesiredFromParams(params, ctx.SSHUsers)
	if err != nil {
		return nil, err
	}
	input, _ := json.Marshal(d)
	ctx.WriteLock.Lock()
	defer ctx.WriteLock.Unlock()
	code, out, errText := ctx.Runner.PrivilegedInput("ssh_keys_apply", input, 120*time.Second)
	return rootAnswer("ssh_keys_apply", code, out, errText)
}

// rootAnswer reads the JSON the root side printed; anything else is a failure with its words.
func rootAnswer(name string, code int, out, errText string) (map[string]any, error) {
	if code != 0 {
		msg := strings.TrimSpace(tail(errText+out, 600))
		if msg == "" {
			msg = fmt.Sprintf("exit %d", code)
		}
		return map[string]any{"ok": false, "error": fmt.Sprintf("%s failed: %s", name, msg)}, nil
	}
	var res map[string]any
	if err := json.Unmarshal([]byte(out), &res); err != nil {
		return map[string]any{"ok": false, "error": fmt.Sprintf("%s gave no answer: %s", name, tail(out+errText, 300))}, nil
	}
	return res, nil
}
