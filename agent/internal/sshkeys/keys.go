// Package sshkeys is what the agent does for SSH logins: it reports which public keys may log in
// as which user, and writes the keys labdex manages. labdex never edits a user's own
// ~/.ssh/authorized_keys: its keys go into root-owned files in /etc/ssh/labdex, one per user
// (<user>.keys) plus the host's break-glass key (<user>.break-glass), which an sshd drop-in adds
// to AuthorizedKeysFile. Writing runs as root (`labdex-agent ssh-keys apply`, started through one
// fixed sudo line) and only for the users agent.conf names in ssh_users.
package sshkeys

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"math/big"
	"regexp"
	"strings"
)

const (
	// Dir holds the files labdex manages; the sshd drop-in points AuthorizedKeysFile at it.
	Dir = "/etc/ssh/labdex"
	// DropIn is the sshd config install.sh writes for --ssh-keys.
	DropIn = "/etc/ssh/sshd_config.d/05-labdex-keys.conf"

	MaxKeysPerUser = 200
	MaxComment     = 100
	MinRSABits     = 3072
)

// Types are the key types labdex accepts. DSA is gone from OpenSSH; RSA below MinRSABits is refused.
var Types = map[string]bool{
	"ssh-ed25519":                        true,
	"ecdsa-sha2-nistp256":                true,
	"ecdsa-sha2-nistp384":                true,
	"ecdsa-sha2-nistp521":                true,
	"sk-ssh-ed25519@openssh.com":         true,
	"sk-ecdsa-sha2-nistp256@openssh.com": true,
	"ssh-rsa":                            true,
}

// UserRe is a user name labdex may manage keys for (what useradd accepts by default, shorter).
var UserRe = regexp.MustCompile(`^[a-z_][a-z0-9_.-]{0,31}$`)

// commentRe keeps comments to plain text: they end up in a root-owned file sshd reads.
var commentRe = regexp.MustCompile(`^[A-Za-z0-9 @._+:=/()-]*$`)

// Key is one public key.
type Key struct {
	Type    string `json:"type"`
	Blob    string `json:"-"` // base64, as in the file
	Comment string `json:"comment"`
	SHA256  string `json:"sha256"` // "SHA256:…", as ssh-keygen -l prints it
	Bits    int    `json:"bits,omitempty"`
}

// Line is the key as it goes into an authorized keys file.
func (k Key) Line() string {
	if k.Comment == "" {
		return k.Type + " " + k.Blob
	}
	return k.Type + " " + k.Blob + " " + k.Comment
}

// Parse reads "<type> <base64> [comment]": a plain key, never options in front of it, because
// options such as command= or from= would let a key do more than the list in labdex shows.
func Parse(line string) (Key, error) {
	line = strings.TrimSpace(line)
	if strings.ContainsAny(line, "\r\n\x00") {
		return Key{}, fmt.Errorf("one key per line")
	}
	f := strings.Fields(line)
	if len(f) < 2 {
		return Key{}, fmt.Errorf("not a public key: want \"<type> <base64> [comment]\"")
	}
	if !Types[f[0]] {
		return Key{}, fmt.Errorf("key type %q is not accepted (options in front of a key aren't either)", head(f[0], 40))
	}
	blob, err := base64.StdEncoding.Strict().DecodeString(f[1])
	if err != nil || len(blob) > 16*1024 {
		return Key{}, fmt.Errorf("the key's base64 part doesn't decode")
	}
	rd := bytes.NewReader(blob)
	inner, err := readString(rd)
	if err != nil || string(inner) != f[0] {
		return Key{}, fmt.Errorf("the key says it is %q inside, not %q", head(string(inner), 40), f[0])
	}
	k := Key{Type: f[0], Blob: f[1], Comment: strings.Join(f[2:], " ")}
	if f[0] == "ssh-rsa" {
		if _, err := readString(rd); err != nil { // e
			return Key{}, fmt.Errorf("a broken RSA key")
		}
		n, err := readString(rd)
		if err != nil {
			return Key{}, fmt.Errorf("a broken RSA key")
		}
		k.Bits = new(big.Int).SetBytes(n).BitLen()
		if k.Bits < MinRSABits {
			return Key{}, fmt.Errorf("RSA keys need %d bits or more, this one has %d", MinRSABits, k.Bits)
		}
	}
	if len([]rune(k.Comment)) > MaxComment || !commentRe.MatchString(k.Comment) {
		return Key{}, fmt.Errorf("the comment may have up to %d letters, digits, spaces and @._+:=/()-", MaxComment)
	}
	sum := sha256.Sum256(blob)
	k.SHA256 = "SHA256:" + base64.RawStdEncoding.EncodeToString(sum[:])
	return k, nil
}

func readString(r *bytes.Reader) ([]byte, error) {
	var n uint32
	if err := binary.Read(r, binary.BigEndian, &n); err != nil {
		return nil, err
	}
	if int(n) > r.Len() {
		return nil, fmt.Errorf("short")
	}
	b := make([]byte, n)
	_, err := r.Read(b)
	return b, err
}

func head(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n]) + "…"
	}
	return s
}

// Found is a line of an authorized keys file, as the report shows it: never the key itself.
type Found struct {
	Key
	Options bool `json:"options,omitempty"` // the line has options (from=, command=, …) in front
}

// ScanFile reads the lines of an authorized keys file. Lines that aren't keys are only counted,
// so a file that points somewhere else never shows what is there.
func ScanFile(data []byte) (keys []Found, other int) {
	for _, raw := range strings.Split(string(data), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if k, ok := scanLine(line); ok {
			keys = append(keys, k)
		} else {
			other++
		}
	}
	return keys, other
}

// scanLine finds the key on a line, with or without options in front. Unlike Parse it accepts
// any comment and RSA size, because it reports what is there rather than deciding what goes in.
func scanLine(line string) (Found, bool) {
	f := strings.Fields(line)
	for i := 0; i+1 < len(f); i++ {
		if !Types[f[i]] && f[i] != "ssh-dss" {
			continue
		}
		blob, err := base64.StdEncoding.DecodeString(f[i+1])
		if err != nil {
			return Found{}, false
		}
		sum := sha256.Sum256(blob)
		k := Key{Type: f[i], Comment: head(strings.Join(f[i+2:], " "), MaxComment),
			SHA256: "SHA256:" + base64.RawStdEncoding.EncodeToString(sum[:])}
		if f[i] == "ssh-rsa" {
			rd := bytes.NewReader(blob)
			if _, err := readString(rd); err == nil {
				if _, err := readString(rd); err == nil {
					if n, err := readString(rd); err == nil {
						k.Bits = new(big.Int).SetBytes(n).BitLen()
					}
				}
			}
		}
		return Found{Key: k, Options: i > 0}, true
	}
	return Found{}, false
}
