package protocol

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

type signingCases struct {
	PublicKey string `json:"public_key"`
	Agent     string `json:"agent"`
	Now       int64  `json:"now"`
	Cases     []struct {
		Name     string         `json:"name"`
		Message  map[string]any `json:"message"`
		Envelope map[string]any `json:"envelope"`
		Error    string         `json:"error"`
	} `json:"cases"`
	Capabilities []struct {
		Type   string         `json:"type"`
		Params map[string]any `json:"params"`
		Needs  []string       `json:"needs"`
	} `json:"capabilities"`
	JobTypes map[string]struct {
		Write   *string `json:"write"`
		Timeout int     `json:"timeout"`
	} `json:"job_types"`
}

func load(t *testing.T) signingCases {
	t.Helper()
	raw, err := os.ReadFile("../../testdata/conformance.json")
	if err != nil {
		t.Fatal(err)
	}
	var all struct {
		Signing signingCases `json:"signing"`
	}
	if err := json.Unmarshal(raw, &all); err != nil {
		t.Fatal(err)
	}
	return all.Signing
}

func TestVerifyConformance(t *testing.T) {
	s := load(t)
	now := time.Unix(s.Now, 0)
	for _, c := range s.Cases {
		env, err := Verify(s.PublicKey, c.Message, s.Agent, now)
		if c.Error != "" {
			if err == nil || err.Error() != c.Error {
				t.Errorf("%s: got %v, want error %q", c.Name, err, c.Error)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", c.Name, err)
			continue
		}
		if !reflect.DeepEqual(env.Raw, c.Envelope) {
			t.Errorf("%s: envelope %v, want %v", c.Name, env.Raw, c.Envelope)
		}
	}
}

func TestCapabilitiesConformance(t *testing.T) {
	s := load(t)
	for _, c := range s.Capabilities {
		got := CapabilitiesFor(c.Type, c.Params)
		if len(got) == 0 && len(c.Needs) == 0 {
			continue
		}
		if !reflect.DeepEqual(got, c.Needs) {
			t.Errorf("%s %v: %v, want %v", c.Type, c.Params, got, c.Needs)
		}
	}
	if len(s.JobTypes) != len(JobTypes) {
		t.Fatalf("job types: %d here, %d in the contract", len(JobTypes), len(s.JobTypes))
	}
	for name, want := range s.JobTypes {
		got, ok := JobTypes[name]
		w := ""
		if want.Write != nil {
			w = *want.Write
		}
		if !ok || got.Write != w || got.Timeout != want.Timeout {
			t.Errorf("job type %s: %+v, want write=%q timeout=%d", name, got, w, want.Timeout)
		}
	}
}

func TestPyRepr(t *testing.T) {
	for in, want := range map[string]string{"shell": "'shell'", "it's": `"it's"`, `a'"b`: `'a\'"b'`} {
		if got := PyRepr(in); got != want {
			t.Errorf("PyRepr(%q) = %s, want %s", in, got, want)
		}
	}
	if PyRepr(nil) != "None" || PyRepr(float64(3)) != "3" {
		t.Error("PyRepr of None or a number")
	}
}
