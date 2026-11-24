package jobs

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

type fake struct {
	root     bool
	programs map[string]bool
	priv     map[string]int
	calls    []string
	out      string
}

func (f *fake) Root() bool        { return f.root }
func (f *fake) Has(p string) bool { return f.programs[p] }
func (f *fake) Run(argv []string, _ time.Duration) (int, string, string) {
	f.calls = append(f.calls, strings.Join(argv, " "))
	return 0, f.out, ""
}
func (f *fake) Privileged(name string, _ time.Duration) (int, string, string) {
	f.calls = append(f.calls, "sudo:"+name)
	return f.priv[name], "", ""
}

func ctx(r *fake) *Context {
	return &Context{Runner: r, Allow: map[string]bool{}, StateDir: os.TempDir(), WriteLock: &sync.Mutex{}}
}

func TestAnsibleParamsConformance(t *testing.T) {
	raw, err := os.ReadFile("../../testdata/conformance.json")
	if err != nil {
		t.Fatal(err)
	}
	var c struct {
		Cases []struct {
			Params map[string]any `json:"params"`
			Error  string         `json:"error"`
		} `json:"ansible_params"`
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	for _, k := range c.Cases {
		got := ""
		if err := CheckAnsibleParams(k.Params); err != nil {
			got = err.Error()
		}
		if got != k.Error {
			t.Errorf("%v: %q, want %q", k.Params, got, k.Error)
		}
	}
}

func targz(t *testing.T, entries map[string]string, extra ...*tar.Header) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(zw)
	_ = tw.WriteHeader(&tar.Header{Typeflag: tar.TypeXGlobalHeader, Name: "pax_global_header", PAXRecords: map[string]string{"comment": "abc"}})
	for name, body := range entries {
		_ = tw.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: int64(len(body)), Typeflag: tar.TypeReg})
		_, _ = tw.Write([]byte(body))
	}
	for _, h := range extra {
		_ = tw.WriteHeader(h)
	}
	_ = tw.Close()
	_ = zw.Close()
	return buf.Bytes()
}

func TestUnpack(t *testing.T) {
	dir := t.TempDir()
	if err := Unpack(targz(t, map[string]string{"ansible/site.yml": "- hosts: all\n"}), dir); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(filepath.Join(dir, "ansible/site.yml"))
	if err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("unpacked file: %v %v", st, err)
	}
	for name, hdr := range map[string]*tar.Header{
		"link":     {Name: "evil", Typeflag: tar.TypeSymlink, Linkname: "/etc/shadow"},
		"absolute": {Name: "/etc/x", Typeflag: tar.TypeReg},
		"dotdot":   {Name: "a/../../x", Typeflag: tar.TypeReg},
	} {
		if err := Unpack(targz(t, nil, hdr), t.TempDir()); err == nil || !strings.Contains(err.Error(), "may not") {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestAnsibleRun(t *testing.T) {
	bundle := targz(t, map[string]string{"ansible/site.yml": "- hosts: all\n"})
	sum := sha256.Sum256(bundle)
	sha := hex.EncodeToString(sum[:])
	r := &fake{root: true, programs: map[string]bool{"ansible-playbook": true}, out: "ok\nPLAY RECAP\nlocalhost: ok=1\n"}
	c := ctx(r)
	c.StateDir = t.TempDir()
	c.FetchBundle = func(s string) ([]byte, error) { return bundle, nil }
	res, err := AnsibleRun(map[string]any{"bundle": sha, "playbook": "ansible/site.yml", "check": true, "commit": "abc"}, c)
	if err != nil || res["ok"] != true || !strings.HasPrefix(res["recap"].(string), "PLAY RECAP") || res["commit"] != "abc" {
		t.Fatalf("%v %v", res, err)
	}
	call := r.calls[0]
	if !strings.HasPrefix(call, "systemd-run --quiet --wait --pipe --collect --unit=labdex-agent-ansible") ||
		!strings.Contains(call, "-i localhost, -c local -e @") || !strings.Contains(call, "--check --diff") {
		t.Fatalf("argv: %s", call)
	}
	if _, err := os.Stat(filepath.Join(c.StateDir, "bundles", sha)); err == nil {
		t.Fatal("the bundle folder is left behind")
	}
	// a bundle that isn't the signed one
	c.FetchBundle = func(s string) ([]byte, error) { return append(bundle, 0), nil }
	if _, err := AnsibleRun(map[string]any{"bundle": sha, "playbook": "ansible/site.yml"}, c); err == nil || err.Error() != "bundle does not match the signed hash" {
		t.Fatal(err)
	}
	r.root = false
	if _, err := AnsibleRun(map[string]any{"bundle": sha, "playbook": "ansible/site.yml"}, c); err == nil || !strings.Contains(err.Error(), "as root") {
		t.Fatal(err)
	}
}

func TestPatchApply(t *testing.T) {
	r := &fake{programs: map[string]bool{"apt-get": true}, priv: map[string]int{}}
	res, err := patchApply(map[string]any{}, ctx(r))
	if err != nil || res["ok"] != true || r.calls[0] != "sudo:apt_update" || r.calls[1] != "sudo:apt_upgrade" {
		t.Fatalf("%v %v %v", res, err, r.calls)
	}
	r = &fake{programs: map[string]bool{"apt-get": true}, priv: map[string]int{"apt_update": 100}}
	res, _ = patchApply(map[string]any{}, ctx(r))
	if res["ok"] != false || res["error"] != "apt_update failed (100)" || len(r.calls) != 1 {
		t.Fatalf("%v %v", res, r.calls)
	}
	if _, err := patchApply(map[string]any{"shell": "x"}, ctx(r)); err == nil || err.Error() != "unknown parameters: shell" {
		t.Fatal(err)
	}
	if _, err := patchApply(map[string]any{}, ctx(&fake{})); err == nil || err.Error() != "no apt-get or dnf on this host" {
		t.Fatal(err)
	}
}

func TestPing(t *testing.T) {
	res, _ := ping(map[string]any{"echo": strings.Repeat("x", 300)}, nil)
	if res["pong"] != true || len(res["echo"].(string)) != 200 {
		t.Fatal(res)
	}
}
