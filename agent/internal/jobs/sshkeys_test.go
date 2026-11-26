package jobs

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/labdex-app/labdex/agent/internal/protocol"
)

const testKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl alice@example.com"

func TestSSHKeysReport(t *testing.T) {
	r := &fake{privOut: map[string]string{"ssh_keys_report": `{"users": [], "uses": true}`}}
	res, err := sshKeysReport(map[string]any{}, ctx(r))
	if err != nil || res["uses"] != true || strings.Join(r.calls, ",") != "sudo:ssh_keys_report" {
		t.Fatalf("%v %v %v", err, res, r.calls)
	}
	if _, err := sshKeysReport(map[string]any{"user": "root"}, ctx(r)); err == nil {
		t.Error("took a parameter")
	}
	r = &fake{priv: map[string]int{"ssh_keys_report": 1}}
	res, _ = sshKeysReport(map[string]any{}, ctx(r))
	if res["ok"] != false {
		t.Errorf("a failed root side: %v", res)
	}
}

func TestSSHKeysApply(t *testing.T) {
	r := &fake{privOut: map[string]string{"ssh_keys_apply": `{"ok": true}`}}
	c := ctx(r)
	c.SSHUsers = []string{"admin"}
	for name, params := range map[string]map[string]any{
		"other user":   {"users": map[string]any{"root": []any{testKey}}},
		"bad key":      {"users": map[string]any{"admin": []any{"command=\"x\" " + testKey}}},
		"no users":     {},
		"extra param":  {"users": map[string]any{}, "path": "/root/.ssh"},
		"glass user":   {"users": map[string]any{}, "break_glass": map[string]any{"user": "root", "key": testKey}},
		"wrong shapes": {"users": []any{"admin"}},
	} {
		_, err := sshKeysApply(params, c)
		var bad *protocol.BadJob
		if err == nil || !errorsAs(err, &bad) {
			t.Errorf("%s: %v", name, err)
		}
	}
	if len(r.calls) != 0 {
		t.Fatalf("asked for root before the checks passed: %v", r.calls)
	}
	res, err := sshKeysApply(map[string]any{"users": map[string]any{"admin": []any{testKey}},
		"break_glass": map[string]any{"user": "admin", "key": testKey}}, c)
	if err != nil || res["ok"] != true {
		t.Fatalf("%v %v", err, res)
	}
	var sent map[string]any
	_ = json.Unmarshal(r.input, &sent)
	if sent["users"].(map[string]any)["admin"].([]any)[0] != testKey || sent["break_glass"] == nil {
		t.Errorf("stdin %s", r.input)
	}
	if got := protocol.CapabilitiesFor("ssh_keys.apply", nil); len(got) != 1 || got[0] != "ssh_keys" {
		t.Errorf("capabilities %v", got)
	}
}

func errorsAs(err error, bad **protocol.BadJob) bool {
	b, ok := err.(*protocol.BadJob)
	*bad = b
	return ok
}
