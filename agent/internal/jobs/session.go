package jobs

import (
	"regexp"
	"time"

	"github.com/labdex-app/labdex/agent/internal/protocol"
)

var sessionID = regexp.MustCompile(`^[A-Za-z0-9_-]{8,64}$`)

// CheckSessionParams checks an ansible.session job: {session, minutes}, nothing else. Neither
// names a port or an address: the pipes only ever reach ssh_port on 127.0.0.1 (agent.conf).
func CheckSessionParams(params map[string]any) error {
	if err := unknownParams(params, "session", "minutes"); err != nil {
		return err
	}
	if s, ok := params["session"].(string); !ok || !sessionID.MatchString(s) {
		return protocol.Bad("session must be 8 to 64 letters, digits, - or _")
	}
	m, ok := params["minutes"].(float64)
	if !ok || m != float64(int64(m)) || m < 1 || m > protocol.MaxSessionMinutes {
		return protocol.Bad("minutes must be a whole number from 1 to %d", protocol.MaxSessionMinutes)
	}
	return nil
}

// AnsibleSession is the ansible.session handler: it only opens the session. The hub then opens
// pipes in it (pipe.open) until it ends (session.end) or its time is up.
func AnsibleSession(params map[string]any, ctx *Context) (map[string]any, error) {
	if err := CheckSessionParams(params); err != nil {
		return nil, err
	}
	if ctx.OpenSession == nil {
		return nil, protocol.Bad("no link to carry a session")
	}
	id := params["session"].(string)
	until := time.Now().Add(time.Duration(params["minutes"].(float64)) * time.Minute)
	if err := ctx.OpenSession(id, until); err != nil {
		return nil, err
	}
	return map[string]any{"ok": true, "session": id, "until": float64(until.Unix())}, nil
}
