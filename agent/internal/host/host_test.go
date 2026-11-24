package host

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestSSHHostKeys(t *testing.T) {
	d := t.TempDir()
	w := func(name, s string) { _ = os.WriteFile(filepath.Join(d, name), []byte(s), 0o644) }
	w("ssh_host_ed25519_key.pub", "ssh-ed25519 AAAAC3Nza root@srv-vm-dns-01\n")
	w("ssh_host_ecdsa_key.pub", "ecdsa-sha2-nistp256 AAAAE2Vj root@srv-vm-dns-01\n")
	w("ssh_host_rsa_key", "-----BEGIN OPENSSH PRIVATE KEY-----\n") // never the private key
	w("ssh_host_dsa_key.pub", "garbage\n")
	want := []string{"ecdsa-sha2-nistp256 AAAAE2Vj", "ssh-ed25519 AAAAC3Nza"}
	if got := SSHHostKeys(d); !reflect.DeepEqual(got, want) {
		t.Fatalf("%v", got)
	}
	if got := SSHHostKeys(filepath.Join(d, "none")); got == nil || len(got) != 0 {
		t.Fatal("no sshd: an empty list, not null")
	}
}
