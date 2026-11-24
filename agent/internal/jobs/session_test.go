package jobs

import (
	"strings"
	"testing"
	"time"

	"github.com/labdex-app/labdex/agent/internal/protocol"
)

func TestSessionParams(t *testing.T) {
	ok := []map[string]any{
		{"session": "run-0001", "minutes": 1.0},
		{"session": strings.Repeat("a", 64), "minutes": 60.0},
	}
	for _, p := range ok {
		if err := CheckSessionParams(p); err != nil {
			t.Errorf("%v: %v", p, err)
		}
	}
	bad := map[string][]map[string]any{
		"unknown parameters: port":       {{"session": "run-0001", "minutes": 5.0, "port": 2222.0}},
		"session must be 8 to 64":        {{"session": "short", "minutes": 5.0}, {"session": "run/../x", "minutes": 5.0}, {"minutes": 5.0}},
		"minutes must be a whole number": {{"session": "run-0001", "minutes": 0.0}, {"session": "run-0001", "minutes": 61.0}, {"session": "run-0001", "minutes": 1.5}, {"session": "run-0001", "minutes": "5"}},
	}
	for want, cases := range bad {
		for _, p := range cases {
			if err := CheckSessionParams(p); err == nil || !strings.HasPrefix(err.Error(), want) {
				t.Errorf("%v: %v, want %q", p, err, want)
			}
		}
	}
}

func TestAnsibleSession(t *testing.T) {
	var gotID string
	var gotUntil time.Time
	ctx := &Context{OpenSession: func(id string, until time.Time) error { gotID, gotUntil = id, until; return nil }}
	res, err := AnsibleSession(map[string]any{"session": "run-0001", "minutes": 10.0}, ctx)
	if err != nil || res["ok"] != true || gotID != "run-0001" || time.Until(gotUntil) < 9*time.Minute {
		t.Fatalf("%v %v %s %v", res, err, gotID, gotUntil)
	}
	if _, err := AnsibleSession(map[string]any{"session": "run-0001", "minutes": 10.0}, &Context{}); err == nil {
		t.Fatal("no link, no session")
	}
	if got := protocol.CapabilitiesFor("ansible.session", nil); len(got) != 1 || got[0] != "ansible" {
		t.Fatalf("needs the ansible capability: %v", got)
	}
}
