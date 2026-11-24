package jobs

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/labdex-app/labdex/agent/internal/protocol"
	"github.com/labdex-app/labdex/agent/internal/runner"
)

// ansible.run: a playbook from git, run on this host only, as a signed bundle. The hub never
// sends a command or a playbook text: the signed job names a bundle by its SHA-256. Ansible is
// Python, so the host needs Python for it; this agent only starts ansible-playbook.

var (
	sha256Re   = regexp.MustCompile(`^[0-9a-f]{64}$`)
	playbookRe = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_./-]*\.ya?ml$`)
)

const (
	MaxBundle   = 8 * 1024 * 1024
	MaxUnpacked = 64 * 1024 * 1024
)

func hasDotDot(p string) bool {
	for _, part := range strings.Split(p, "/") {
		if part == ".." {
			return true
		}
	}
	return false
}

// CheckAnsibleParams checks the parameters of an ansible.run job.
func CheckAnsibleParams(params map[string]any) error {
	if err := unknownParams(params, "bundle", "playbook", "commit", "vars", "check"); err != nil {
		return err
	}
	if !sha256Re.MatchString(pyStr(orEmpty(params["bundle"]))) {
		return protocol.Bad("bundle must be a SHA-256")
	}
	pb := pyStr(orEmpty(params["playbook"]))
	if !playbookRe.MatchString(pb) || hasDotDot(pb) {
		return protocol.Bad("playbook must be a relative .yml path inside the bundle")
	}
	if v, ok := params["vars"]; ok {
		if _, isMap := v.(map[string]any); !isMap {
			return protocol.Bad("vars must be an object")
		}
	}
	return nil
}

func orEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}

type member struct {
	name string
	dir  bool
	data []byte
}

// Unpack unpacks a bundle safely: regular files and folders only, all inside dest.
func Unpack(data []byte, dest string) error {
	zr, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return protocol.Bad("bundle is not a .tar.gz: %v", err)
	}
	tr := tar.NewReader(zr)
	var members []member
	var total int64
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return protocol.Bad("bundle can't be read: %v", err)
		}
		if h.Typeflag == tar.TypeXGlobalHeader { // git archive's pax header: not a member
			continue
		}
		name := h.Name
		if strings.HasPrefix(name, "./") {
			name = strings.TrimLeft(name, "./")
		}
		isFile := h.Typeflag == tar.TypeReg || h.Typeflag == tar.TypeRegA || h.Typeflag == tar.TypeCont
		isDir := h.Typeflag == tar.TypeDir
		if strings.HasPrefix(name, "/") || hasDotDot(name) || !(isFile || isDir) {
			return protocol.Bad("bundle holds a path it may not: %s", h.Name)
		}
		total += h.Size
		if total > MaxUnpacked {
			return protocol.Bad("bundle too large unpacked")
		}
		m := member{name: h.Name, dir: isDir}
		if isFile {
			m.data, err = io.ReadAll(io.LimitReader(tr, MaxUnpacked+1))
			if err != nil {
				return protocol.Bad("bundle can't be read: %v", err)
			}
		}
		members = append(members, m)
	}
	root, err := filepath.Abs(dest)
	if err != nil {
		return err
	}
	for _, m := range members {
		target := filepath.Join(root, m.name)
		if rel, err := filepath.Rel(root, target); err != nil || rel == ".." || strings.HasPrefix(rel, "../") {
			return protocol.Bad("bundle path leaves its folder: %s", m.name)
		}
		if m.dir {
			if err := os.MkdirAll(target, 0o700); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		if err := os.WriteFile(target, m.data, 0o600); err != nil {
			return err
		}
		if err := os.Chmod(target, 0o600); err != nil {
			return err
		}
	}
	return nil
}

// AnsibleRun is the ansible.run handler.
func AnsibleRun(params map[string]any, ctx *Context) (map[string]any, error) {
	if err := CheckAnsibleParams(params); err != nil {
		return nil, err
	}
	if !ctx.Runner.Root() {
		return nil, protocol.Bad("ansible.run needs the agent to run as root on this host (install with --root)")
	}
	if !ctx.Runner.Has("ansible-playbook") {
		return nil, protocol.Bad("ansible-playbook is not installed on this host")
	}
	if ctx.FetchBundle == nil {
		return nil, protocol.Bad("no link to fetch the bundle over")
	}
	sha := params["bundle"].(string)
	data, err := ctx.FetchBundle(sha)
	if err != nil {
		return nil, err
	}
	if len(data) > MaxBundle {
		return nil, protocol.Bad("bundle too large")
	}
	sum := sha256.Sum256(data)
	if hex.EncodeToString(sum[:]) != sha {
		return nil, protocol.Bad("bundle does not match the signed hash")
	}
	work := filepath.Join(ctx.StateDir, "bundles", sha)
	_ = os.RemoveAll(work)
	if err := os.MkdirAll(work, 0o700); err != nil {
		return nil, err
	}
	defer os.RemoveAll(work)
	if err := Unpack(data, work); err != nil {
		return nil, err
	}
	pb := params["playbook"].(string)
	playbook := filepath.Join(work, pb)
	if st, err := os.Stat(playbook); err != nil || !st.Mode().IsRegular() {
		return nil, protocol.Bad("%s is not in the bundle", pb)
	}
	vars, _ := params["vars"].(map[string]any)
	if vars == nil {
		vars = map[string]any{}
	}
	varfile := filepath.Join(work, ".labdex-vars.json")
	vb, _ := json.Marshal(vars)
	if err := os.WriteFile(varfile, vb, 0o600); err != nil {
		return nil, err
	}
	check := protocol.Truthy(params["check"])
	bin := runner.Which("ansible-playbook", runner.SearchPath)
	if bin == "" {
		bin = "ansible-playbook"
	}
	argv := []string{"systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--unit=labdex-agent-ansible",
		"--working-directory=" + work, "--setenv=ANSIBLE_NOCOLOR=1", "--setenv=ANSIBLE_RETRY_FILES_ENABLED=0",
		// absolute: systemd-run looks programs up on its own PATH, and pip puts ansible in /usr/local/bin
		bin, "-i", "localhost,", "-c", "local", "-e", "@" + varfile}
	if check {
		argv = append(argv, "--check", "--diff")
	}
	argv = append(argv, playbook)
	ctx.WriteLock.Lock()
	code, out, errText := ctx.Runner.Run(argv, 3300*time.Second)
	ctx.WriteLock.Unlock()
	recap := ""
	if i := strings.LastIndex(out, "PLAY RECAP"); i >= 0 {
		recap = head(out[i:], 2000)
	}
	commit := ""
	if v, ok := params["commit"]; ok {
		commit = pyStr(v)
	}
	return map[string]any{"ok": code == 0, "code": code, "check": check, "commit": commit,
		"recap": recap, "output": tail(out+errText, 20000)}, nil
}
