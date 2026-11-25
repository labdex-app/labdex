package sshkeys

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/binary"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func wire(parts ...[]byte) []byte {
	var b bytes.Buffer
	for _, p := range parts {
		_ = binary.Write(&b, binary.BigEndian, uint32(len(p)))
		b.Write(p)
	}
	return b.Bytes()
}

func edKey(t *testing.T, comment string) string {
	t.Helper()
	pub, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	line := "ssh-ed25519 " + base64.StdEncoding.EncodeToString(wire([]byte("ssh-ed25519"), pub))
	if comment != "" {
		line += " " + comment
	}
	return line
}

func rsaKey(t *testing.T, bits int) string {
	t.Helper()
	k, err := rsa.GenerateKey(rand.Reader, bits)
	if err != nil {
		t.Fatal(err)
	}
	e := big.NewInt(int64(k.E)).Bytes()
	n := append([]byte{0}, k.N.Bytes()...)
	return "ssh-rsa " + base64.StdEncoding.EncodeToString(wire([]byte("ssh-rsa"), e, n))
}

func TestParse(t *testing.T) {
	good := edKey(t, "alice@example.com")
	k, err := Parse(good)
	if err != nil {
		t.Fatal(err)
	}
	if k.Type != "ssh-ed25519" || k.Comment != "alice@example.com" || !strings.HasPrefix(k.SHA256, "SHA256:") || k.Line() != good {
		t.Fatalf("parsed %+v", k)
	}
	blob := strings.Fields(good)[1]
	for name, line := range map[string]string{
		"options":       `command="/bin/sh" ` + good,
		"two lines":     good + "\n" + good,
		"unknown type":  "ssh-dss " + blob,
		"wrong inside":  "ecdsa-sha2-nistp256 " + blob,
		"bad base64":    "ssh-ed25519 !!!",
		"just a type":   "ssh-ed25519",
		"odd comment":   good + " $(reboot)",
		"short rsa key": rsaKey(t, 2048),
	} {
		if _, err := Parse(line); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	if k, err := Parse(rsaKey(t, 3072)); err != nil || k.Bits != 3072 {
		t.Errorf("rsa 3072: %v %+v", err, k)
	}
}

func TestScanFile(t *testing.T) {
	a, b := edKey(t, "a"), edKey(t, "")
	data := "# a comment\n" + a + "\n" + `from="192.0.2.1",no-pty ` + b + "\nroot:x:0:0:root:/root:/bin/bash\n\n"
	keys, other := ScanFile([]byte(data))
	if len(keys) != 2 || other != 1 || keys[0].Options || !keys[1].Options || keys[0].Comment != "a" {
		t.Fatalf("keys %+v other %d", keys, other)
	}
}

func testHost(t *testing.T) *Host {
	t.Helper()
	root := t.TempDir()
	for _, d := range []string{"etc/ssh/sshd_config.d", "home/admin/.ssh", "home/deploy", "root"} {
		if err := os.MkdirAll(filepath.Join(root, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	passwd := "root:x:0:0:root:/root:/bin/bash\n" +
		"daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n" +
		"admin:x:1000:1000::/home/admin:/bin/bash\n" +
		"deploy:x:1001:1001::/home/deploy:/bin/bash\n" +
		"svc:x:1002:1002::/home/svc:/usr/sbin/nologin\n"
	if err := os.WriteFile(filepath.Join(root, "etc/passwd"), []byte(passwd), 0o644); err != nil {
		t.Fatal(err)
	}
	return &Host{Root: root, SSHD: func() (string, error) {
		return "port 22\nauthorizedkeysfile .ssh/authorized_keys /etc/ssh/labdex/%u.keys /etc/ssh/labdex/%u.break-glass\npasswordauthentication no\n", nil
	}}
}

func TestReport(t *testing.T) {
	h := testHost(t)
	own := edKey(t, "laptop")
	_ = os.WriteFile(filepath.Join(h.Root, "home/admin/.ssh/authorized_keys"), []byte(own+"\n"), 0o600)
	// a link elsewhere is never read
	_ = os.MkdirAll(filepath.Join(h.Root, "home/deploy/.ssh"), 0o755)
	_ = os.Symlink(filepath.Join(h.Root, "etc/passwd"), filepath.Join(h.Root, "home/deploy/.ssh/authorized_keys"))
	r := h.Report([]string{"admin", "ghost"})
	names := []string{}
	for _, u := range r.Users {
		names = append(names, u.User)
	}
	if strings.Join(names, ",") != "admin,deploy,ghost,root" {
		t.Fatalf("users %v", names)
	}
	admin, deploy, ghost := r.Users[0], r.Users[1], r.Users[2]
	if !admin.Managed || len(admin.Own) != 1 || len(admin.Own[0].Keys) != 1 || admin.Own[0].Keys[0].Comment != "laptop" {
		t.Errorf("admin %+v", admin)
	}
	if deploy.Managed || len(deploy.Own) != 1 || deploy.Own[0].Error == "" || len(deploy.Own[0].Keys) != 0 {
		t.Errorf("deploy %+v", deploy.Own[0])
	}
	if !ghost.Missing {
		t.Errorf("ghost %+v", ghost)
	}
	if !r.Uses || r.Exclusive || r.SSHD["passwordauthentication"] != "no" {
		t.Errorf("sshd %+v uses %v exclusive %v", r.SSHD, r.Uses, r.Exclusive)
	}
}

func TestApply(t *testing.T) {
	h := testHost(t)
	users := []string{"admin", "deploy"}
	a, b, glass := edKey(t, "alice"), edKey(t, "bob"), edKey(t, "break-glass")

	if _, err := h.Apply(Desired{Users: map[string][]string{"root": {a}}}, users); err == nil {
		t.Fatal("wrote keys for a user outside ssh_users")
	}
	if _, err := h.Apply(Desired{Users: map[string][]string{}}, nil); err == nil {
		t.Fatal("wrote keys without ssh_users")
	}
	if _, err := h.Apply(Desired{Users: map[string][]string{"admin": {`command="x" ` + a}}}, users); err == nil {
		t.Fatal("accepted a key with options")
	}

	// a stale file of a user no longer managed must go
	_ = os.MkdirAll(filepath.Join(h.Root, Dir), 0o755)
	_ = os.WriteFile(filepath.Join(h.Root, Dir, "old.keys"), []byte(b+"\n"), 0o644)

	res, err := h.Apply(Desired{Users: map[string][]string{"admin": {a, b, a}},
		BreakGlass: &BreakGlass{User: "admin", Key: glass}}, users)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Changes) != 2 || res.Changes[0].Keys != 2 || len(res.Changes[0].Added) != 2 || res.Changes[1].Keys != 0 {
		t.Fatalf("changes %+v", res.Changes)
	}
	if res.BreakGlass != "set for admin" {
		t.Errorf("break glass %q", res.BreakGlass)
	}
	body, _ := os.ReadFile(filepath.Join(h.Root, Dir, "admin.keys"))
	if !strings.HasPrefix(string(body), "# Written by labdex") || !strings.Contains(string(body), a+"\n"+b+"\n") {
		t.Errorf("admin.keys:\n%s", body)
	}
	if st, _ := os.Stat(filepath.Join(h.Root, Dir, "admin.keys")); st.Mode().Perm() != 0o644 {
		t.Errorf("mode %v", st.Mode())
	}
	if _, err := os.Stat(filepath.Join(h.Root, Dir, "old.keys")); !os.IsNotExist(err) {
		t.Error("old.keys still there")
	}

	// no break_glass: the key stays; a new user for it: the old file goes after the new is written
	res, err = h.Apply(Desired{Users: map[string][]string{"admin": {a}}}, users)
	if err != nil || len(res.Changes[0].Removed) != 1 || res.BreakGlass != "" {
		t.Fatalf("second apply %v %+v", err, res)
	}
	if _, err := os.Stat(filepath.Join(h.Root, Dir, "admin.break-glass")); err != nil {
		t.Fatal("break-glass key removed without a new one")
	}
	if _, err = h.Apply(Desired{Users: map[string][]string{}, BreakGlass: &BreakGlass{User: "deploy", Key: glass}}, users); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(h.Root, Dir, "admin.break-glass")); !os.IsNotExist(err) {
		t.Error("the old break-glass file is still there")
	}
	if _, err := os.Stat(filepath.Join(h.Root, Dir, "deploy.break-glass")); err != nil {
		t.Error("no break-glass file for deploy")
	}
}

func TestApplyRefusesAWritableFolder(t *testing.T) {
	h := testHost(t)
	_ = os.MkdirAll(filepath.Join(h.Root, Dir), 0o777)
	_ = os.Chmod(filepath.Join(h.Root, Dir), 0o777)
	if _, err := h.Apply(Desired{Users: map[string][]string{}}, []string{"admin"}); err == nil {
		t.Fatal("wrote into a folder others may write")
	}
}

func TestReadDesired(t *testing.T) {
	if _, err := ReadDesired(strings.NewReader(`{"users": {}, "shell": "x"}`)); err == nil {
		t.Error("unknown field accepted")
	}
	d, err := ReadDesired(strings.NewReader(`{"users": {"admin": []}}`))
	if err != nil || d.Users["admin"] == nil {
		t.Errorf("%v %+v", err, d)
	}
}
