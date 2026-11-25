package sshkeys

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// What runs as root: `labdex-agent ssh-keys report` and `labdex-agent ssh-keys apply`. Both are
// started by the agent through fixed sudo lines, so they trust nothing the agent's user could
// change: the users come from agent.conf (root-owned), files are opened without following links.

const maxFile = 1 << 20

// Host is where the root side looks: "/" on a host, a temporary folder in tests.
type Host struct {
	Root string
	// SSHD runs `sshd -T` and returns its output; nil skips it.
	SSHD func() (string, error)
}

// System is this host.
func System() *Host { return &Host{Root: "/", SSHD: sshdT} }

func (h *Host) path(p string) string { return filepath.Join(h.Root, p) }

func sshdT() (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	for _, p := range []string{"/usr/sbin/sshd", "/usr/bin/sshd", "/sbin/sshd"} {
		if _, err := os.Stat(p); err == nil {
			cmd := exec.CommandContext(ctx, p, "-T")
			cmd.Env = []string{"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL=C"}
			out, err := cmd.Output()
			return string(out), err
		}
	}
	return "", errors.New("sshd is not installed")
}

// Account is one line of /etc/passwd.
type Account struct {
	Name  string
	UID   int
	Home  string
	Shell string
}

// Accounts reads /etc/passwd.
func (h *Host) Accounts() map[string]Account {
	out := map[string]Account{}
	f, err := os.Open(h.path("/etc/passwd"))
	if err != nil {
		return out
	}
	defer func() { _ = f.Close() }()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		p := strings.Split(sc.Text(), ":")
		if len(p) < 7 {
			continue
		}
		uid, err := strconv.Atoi(p[2])
		if err != nil {
			continue
		}
		out[p[0]] = Account{Name: p[0], UID: uid, Home: p[5], Shell: p[6]}
	}
	return out
}

// canLogIn: root, and people (uid 1000 and up) with a real shell.
func canLogIn(a Account) bool {
	if a.UID != 0 && (a.UID < 1000 || a.UID == 65534) {
		return false
	}
	return !strings.HasSuffix(a.Shell, "nologin") && !strings.HasSuffix(a.Shell, "/false") && a.Shell != ""
}

// readNoFollow reads a regular file without following a link at its last part.
func readNoFollow(path string) ([]byte, error) {
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !st.Mode().IsRegular() {
		return nil, fmt.Errorf("not a regular file")
	}
	return io.ReadAll(io.LimitReader(f, maxFile))
}

// File is what one authorized keys file holds.
type File struct {
	Path  string  `json:"path"`
	Keys  []Found `json:"keys"`
	Other int     `json:"other,omitempty"` // lines that are no key
	Error string  `json:"error,omitempty"`
}

func (h *Host) scan(path string) *File {
	data, err := readNoFollow(h.path(path))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	f := &File{Path: path, Keys: []Found{}}
	if err != nil {
		f.Error = err.Error()
		if errors.Is(err, syscall.ELOOP) {
			f.Error = "a symbolic link: not read"
		}
		return f
	}
	f.Keys, f.Other = ScanFile(data)
	if f.Keys == nil {
		f.Keys = []Found{}
	}
	return f
}

// UserReport is one user who can log in, or whom labdex manages.
type UserReport struct {
	User       string  `json:"user"`
	UID        int     `json:"uid"`
	Managed    bool    `json:"managed"` // in ssh_users
	Own        []*File `json:"own"`     // ~/.ssh/authorized_keys and authorized_keys2
	Labdex     *File   `json:"labdex,omitempty"`
	BreakGlass *File   `json:"break_glass,omitempty"`
	Missing    bool    `json:"missing,omitempty"` // in ssh_users, but no such user here
}

// Report is the host's SSH login keys and the sshd settings that decide which files count.
type Report struct {
	Users     []UserReport      `json:"users"`
	SSHD      map[string]string `json:"sshd"`
	SSHDError string            `json:"sshd_error,omitempty"`
	DropIn    bool              `json:"drop_in"`   // the labdex sshd drop-in is there
	Uses      bool              `json:"uses"`      // sshd reads labdex's files
	Exclusive bool              `json:"exclusive"` // …and not the users' own files
	At        int64             `json:"at"`
}

// SSHDKeys are the sshd -T settings the report shows.
var SSHDKeys = []string{"authorizedkeysfile", "passwordauthentication", "kbdinteractiveauthentication",
	"permitrootlogin", "pubkeyauthentication", "trustedusercakeys", "authorizedprincipalsfile"}

// Report reads every login user's keys. users are agent.conf's ssh_users.
func (h *Host) Report(users []string) Report {
	r := Report{Users: []UserReport{}, SSHD: map[string]string{}, At: time.Now().Unix()}
	managed := map[string]bool{}
	for _, u := range users {
		managed[u] = true
	}
	accounts := h.Accounts()
	names := []string{}
	for n, a := range accounts {
		if canLogIn(a) || managed[n] {
			names = append(names, n)
		}
	}
	for u := range managed {
		if _, ok := accounts[u]; !ok {
			names = append(names, u)
		}
	}
	sort.Strings(names)
	for _, n := range names {
		a, ok := accounts[n]
		ur := UserReport{User: n, UID: a.UID, Managed: managed[n], Own: []*File{}, Missing: !ok}
		if ok && a.Home != "" && strings.HasPrefix(a.Home, "/") {
			sshDir := filepath.Join(a.Home, ".ssh")
			if st, err := os.Lstat(h.path(sshDir)); err == nil && st.Mode()&os.ModeSymlink != 0 {
				ur.Own = append(ur.Own, &File{Path: sshDir, Keys: []Found{}, Error: "a symbolic link: not read"})
			} else {
				for _, name := range []string{"authorized_keys", "authorized_keys2"} {
					if f := h.scan(filepath.Join(sshDir, name)); f != nil {
						ur.Own = append(ur.Own, f)
					}
				}
			}
		}
		ur.Labdex = h.scan(filepath.Join(Dir, n+".keys"))
		ur.BreakGlass = h.scan(filepath.Join(Dir, n+".break-glass"))
		r.Users = append(r.Users, ur)
	}
	if _, err := os.Lstat(h.path(DropIn)); err == nil {
		r.DropIn = true
	}
	if h.SSHD != nil {
		out, err := h.SSHD()
		if err != nil {
			r.SSHDError = head(strings.TrimSpace(err.Error()), 300)
		}
		for _, line := range strings.Split(out, "\n") {
			k, v, ok := strings.Cut(strings.TrimSpace(line), " ")
			for _, want := range SSHDKeys {
				if ok && k == want {
					r.SSHD[k] = v
				}
			}
		}
		files := strings.Fields(r.SSHD["authorizedkeysfile"])
		for _, f := range files {
			if strings.HasPrefix(f, Dir+"/") {
				r.Uses = true
			}
		}
		r.Exclusive = r.Uses
		for _, f := range files {
			if !strings.HasPrefix(f, Dir+"/") {
				r.Exclusive = false
			}
		}
	}
	return r
}

// Desired is what `ssh-keys apply` reads on stdin: every managed user's keys (a user left out
// gets none), and optionally a new break-glass key.
type Desired struct {
	Users      map[string][]string `json:"users"`
	BreakGlass *BreakGlass         `json:"break_glass,omitempty"`
}

// BreakGlass is the host's emergency key, for one user.
type BreakGlass struct {
	User string `json:"user"`
	Key  string `json:"key"`
}

// Change is what apply did for one user.
type Change struct {
	User    string   `json:"user"`
	Keys    int      `json:"keys"`
	Added   []string `json:"added"`
	Removed []string `json:"removed"`
}

// Applied is apply's answer.
type Applied struct {
	OK         bool     `json:"ok"`
	Changes    []Change `json:"changes"`
	BreakGlass string   `json:"break_glass,omitempty"` // "set for <user>" when it changed
	Report     Report   `json:"report"`
}

// Check checks a Desired against agent.conf's users. The agent runs it before it asks for root,
// and the root side runs it again.
func Check(d Desired, users []string) error {
	allowed := map[string]bool{}
	for _, u := range users {
		allowed[u] = true
	}
	if len(users) == 0 {
		return fmt.Errorf("agent.conf names no ssh_users: labdex may manage nobody's keys here")
	}
	var bad []string
	for u, keys := range d.Users {
		if !allowed[u] {
			bad = append(bad, u)
			continue
		}
		if len(keys) > MaxKeysPerUser {
			return fmt.Errorf("%s: at most %d keys", u, MaxKeysPerUser)
		}
		for i, k := range keys {
			if _, err := Parse(k); err != nil {
				return fmt.Errorf("%s, key %d: %v", u, i+1, err)
			}
		}
	}
	if len(bad) > 0 {
		sort.Strings(bad)
		return fmt.Errorf("not in agent.conf's ssh_users: %s", strings.Join(bad, ", "))
	}
	if b := d.BreakGlass; b != nil {
		if !allowed[b.User] {
			return fmt.Errorf("break-glass user %s is not in agent.conf's ssh_users", b.User)
		}
		if _, err := Parse(b.Key); err != nil {
			return fmt.Errorf("break-glass key: %v", err)
		}
	}
	return nil
}

const header = "# Written by labdex (labdex-agent ssh-keys apply). Edits here are overwritten; change the keys in labdex.\n"

// Apply writes the desired keys. users are agent.conf's ssh_users.
func (h *Host) Apply(d Desired, users []string) (Applied, error) {
	if err := Check(d, users); err != nil {
		return Applied{}, err
	}
	accounts := h.Accounts()
	if err := h.ensureDir(); err != nil {
		return Applied{}, err
	}
	out := Applied{OK: true, Changes: []Change{}}
	for _, u := range sortedCopy(users) {
		if _, ok := accounts[u]; !ok {
			continue // reported as missing; nothing to log in as
		}
		before := map[string]bool{}
		if f := h.scan(filepath.Join(Dir, u+".keys")); f != nil {
			for _, k := range f.Keys {
				before[k.SHA256] = true
			}
		}
		seen := map[string]bool{}
		var lines []string
		ch := Change{User: u, Added: []string{}, Removed: []string{}}
		for _, raw := range d.Users[u] {
			k, _ := Parse(raw)
			if seen[k.SHA256] {
				continue
			}
			seen[k.SHA256] = true
			lines = append(lines, k.Line())
			if !before[k.SHA256] {
				ch.Added = append(ch.Added, k.SHA256)
			}
		}
		for fp := range before {
			if !seen[fp] {
				ch.Removed = append(ch.Removed, fp)
			}
		}
		sort.Strings(ch.Removed)
		ch.Keys = len(lines)
		if err := h.write(u+".keys", lines); err != nil {
			return Applied{}, err
		}
		out.Changes = append(out.Changes, ch)
	}
	// files of users labdex no longer manages: their keys must stop working
	if entries, err := os.ReadDir(h.path(Dir)); err == nil {
		keep := map[string]bool{}
		for _, u := range users {
			keep[u+".keys"] = true
		}
		for _, e := range entries {
			if strings.HasSuffix(e.Name(), ".keys") && !keep[e.Name()] {
				_ = os.Remove(h.path(filepath.Join(Dir, e.Name())))
			}
		}
	}
	if b := d.BreakGlass; b != nil {
		k, _ := Parse(b.Key)
		changed := true
		if f := h.scan(filepath.Join(Dir, b.User+".break-glass")); f != nil && len(f.Keys) == 1 && f.Keys[0].SHA256 == k.SHA256 {
			changed = false
		}
		if changed {
			if err := h.write(b.User+".break-glass", []string{k.Line()}); err != nil {
				return Applied{}, err
			}
			out.BreakGlass = "set for " + b.User
		}
		// one break-glass key per host: an older one for another user goes, but only now that
		// the new one is written
		if entries, err := os.ReadDir(h.path(Dir)); err == nil {
			for _, e := range entries {
				if strings.HasSuffix(e.Name(), ".break-glass") && e.Name() != b.User+".break-glass" {
					_ = os.Remove(h.path(filepath.Join(Dir, e.Name())))
				}
			}
		}
	}
	out.Report = h.Report(users)
	return out, nil
}

func sortedCopy(in []string) []string {
	out := append([]string(nil), in...)
	sort.Strings(out)
	return out
}

// ensureDir makes /etc/ssh/labdex: a real folder, root's, not writable by others.
func (h *Host) ensureDir() error {
	p := h.path(Dir)
	st, err := os.Lstat(p)
	if errors.Is(err, os.ErrNotExist) {
		if err := os.Mkdir(p, 0o755); err != nil {
			return err
		}
		st, err = os.Lstat(p)
	}
	if err != nil {
		return err
	}
	if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("%s is not a folder", Dir)
	}
	if st.Mode().Perm()&0o022 != 0 {
		return fmt.Errorf("%s may be written by others than root: fix its mode (0755)", Dir)
	}
	if s, ok := st.Sys().(*syscall.Stat_t); ok && h.Root == "/" && s.Uid != 0 {
		return fmt.Errorf("%s does not belong to root", Dir)
	}
	return nil
}

// write replaces a file in Dir at once: a new file beside it, synced, renamed over the old one.
func (h *Host) write(name string, lines []string) error {
	final := h.path(filepath.Join(Dir, name))
	tmp := final + ".new"
	_ = os.Remove(tmp)
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0o644)
	if err != nil {
		return err
	}
	body := header + strings.Join(lines, "\n")
	if len(lines) > 0 {
		body += "\n"
	}
	if _, err = f.WriteString(body); err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Chmod(tmp, 0o644) // the umask must not make it unreadable for sshd
	}
	if err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, final)
}

// ReadDesired reads apply's input: one JSON object, at most 1 MiB, nothing unknown in it.
func ReadDesired(r io.Reader) (Desired, error) {
	var d Desired
	dec := json.NewDecoder(io.LimitReader(r, maxFile))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&d); err != nil {
		return d, fmt.Errorf("bad input: %v", err)
	}
	if d.Users == nil {
		d.Users = map[string][]string{}
	}
	return d, nil
}
