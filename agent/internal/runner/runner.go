// Package runner runs the commands jobs need, and holds the only command lines the agent may run
// with privileges. There is no shell anywhere: a privileged step is one of the fixed lines in
// Privileged, and `labdex-agent sudoers` prints the sudo rules for exactly those lines.
package runner

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// SearchPath is where programs are looked up on the host.
const SearchPath = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

// systemPath is where the program that systemd-run starts is looked up, and the PATH jobs get.
const systemPath = "/usr/sbin:/usr/bin:/sbin:/bin"

var (
	systemdRun = []string{"systemd-run", "--quiet", "--wait", "--pipe", "--collect"}
	aptOpts    = []string{"-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"}
)

// Line is one privileged command: the capability it needs ("" for read-only) and its argv.
type Line struct {
	Name       string
	Capability string
	Argv       []string
}

func cat(parts ...[]string) []string {
	var out []string
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

// Privileged is every command the agent may run as root, in the order sudoers lists them.
var Privileged = []Line{
	{"ss", "", []string{"ss", "-Htulpn"}},
	{"docker_ps", "", []string{"docker", "ps", "--no-trunc", "--format", "{{json .}}"}},
	{"podman_ps", "", []string{"podman", "ps", "--format", "json"}},
	{"apt_update", "patch", cat(systemdRun, []string{"--unit=labdex-agent-patch", "--setenv=DEBIAN_FRONTEND=noninteractive",
		"apt-get", "-q", "update"})},
	{"apt_upgrade", "patch", cat(systemdRun, []string{"--unit=labdex-agent-patch", "--setenv=DEBIAN_FRONTEND=noninteractive",
		"apt-get", "-q", "-y"}, aptOpts, []string{"dist-upgrade"})},
	{"dnf_upgrade", "patch", cat(systemdRun, []string{"--unit=labdex-agent-patch", "dnf", "-q", "-y", "upgrade"})},
	{"reboot", "reboot", []string{"shutdown", "-r", "+1", "labdex-agent: reboot after updates"}},
}

// Lookup finds a privileged line by name.
func Lookup(name string) (Line, bool) {
	for _, l := range Privileged {
		if l.Name == name {
			return l, true
		}
	}
	return Line{}, false
}

// Which finds a program on a colon-separated path, like Python's shutil.which.
func Which(program, path string) string {
	if strings.Contains(program, "/") {
		if executable(program) {
			return program
		}
		return ""
	}
	for _, dir := range filepath.SplitList(path) {
		p := filepath.Join(dir, program)
		if executable(p) {
			return p
		}
	}
	return ""
}

func executable(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir() && st.Mode()&0o111 != 0
}

// Resolve gives absolute paths for the program and, for systemd-run, the program it starts.
// nil when either is missing on this host.
func Resolve(argv []string) []string {
	out := append([]string(nil), argv...)
	first := Which(out[0], SearchPath)
	if first == "" {
		return nil
	}
	out[0] = first
	if argv[0] == "systemd-run" {
		for i := 1; i < len(out); i++ {
			if !strings.HasPrefix(out[i], "-") {
				prog := Which(out[i], systemPath)
				if prog == "" {
					return nil
				}
				out[i] = prog
				break
			}
		}
	}
	return out
}

// Missing names the program of a line that this host lacks.
func Missing(argv []string) string {
	if Which(argv[0], SearchPath) == "" {
		return argv[0]
	}
	for _, a := range argv[1:] {
		if !strings.HasPrefix(a, "-") {
			return a
		}
	}
	return argv[0]
}

// Runner runs commands for jobs. Tests swap it for a fake.
type Runner interface {
	// Run runs argv with no stdin and a fixed environment: (exit code, stdout, stderr).
	// 127: not found, 124: timed out, 126: couldn't start.
	Run(argv []string, timeout time.Duration) (int, string, string)
	// Privileged runs one of the Privileged lines: directly as root, else through `sudo -n`.
	Privileged(name string, timeout time.Duration) (int, string, string)
	// Has reports whether a program is installed.
	Has(program string) bool
	// Root reports whether the agent runs as root.
	Root() bool
}

// Host runs commands on this host.
type Host struct{ IsRoot bool }

// New is a runner for this host; root is taken from the effective uid.
func New() *Host { return &Host{IsRoot: os.Geteuid() == 0} }

func (h *Host) Root() bool { return h.IsRoot }

func (h *Host) Has(program string) bool { return Which(program, SearchPath) != "" }

func (h *Host) Run(argv []string, timeout time.Duration) (int, string, string) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	if !strings.Contains(argv[0], "/") {
		// exec looks programs up on the agent's own PATH; use the fixed one jobs get
		if p := Which(argv[0], systemPath); p != "" {
			cmd.Path = p
		} else {
			return 127, "", argv[0] + ": not found"
		}
	}
	cmd.Env = []string{"PATH=" + systemPath, "LC_ALL=C"}
	cmd.Stdin = nil
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	cmd.WaitDelay = 5 * time.Second
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return 124, "", fmt.Sprintf("%s: timed out after %.0f s", argv[0], timeout.Seconds())
	}
	var exit *exec.ExitError
	switch {
	case err == nil:
		return 0, stdout.String(), stderr.String()
	case errors.As(err, &exit):
		return exit.ExitCode(), stdout.String(), stderr.String()
	case errors.Is(err, exec.ErrNotFound), errors.Is(err, os.ErrNotExist):
		return 127, "", argv[0] + ": not found"
	default:
		return 126, "", err.Error()
	}
}

func (h *Host) Privileged(name string, timeout time.Duration) (int, string, string) {
	line, ok := Lookup(name)
	if !ok {
		return 126, "", name + ": not a privileged command"
	}
	full := Resolve(line.Argv)
	if full == nil {
		return 127, "", line.Argv[0] + ": not installed"
	}
	if h.IsRoot {
		return h.Run(full, timeout)
	}
	return h.Run(append([]string{"sudo", "-n"}, full...), timeout)
}

var sudoersSpecial = strings.NewReplacer(`\`, `\\`, ",", `\,`, ":", `\:`, "=", `\=`)

// Sudoers is the sudo rules for this host: every read-only line, and the write lines of
// capabilities the host switched on.
func Sudoers(user string, allow map[string]bool) string {
	lines := []string{"# labdex-agent: written by `labdex-agent sudoers`; the agent runs nothing else with sudo.",
		"Defaults:" + user + " !requiretty, !lecture"}
	for _, l := range Privileged {
		if l.Capability != "" && !allow[l.Capability] {
			continue
		}
		full := Resolve(l.Argv)
		if full == nil {
			lines = append(lines, "# "+l.Name+": "+Missing(l.Argv)+" is not installed here")
			continue
		}
		esc := make([]string, len(full))
		for i, a := range full {
			esc[i] = sudoersSpecial.Replace(a)
		}
		lines = append(lines, user+" ALL=(root) NOPASSWD: "+strings.Join(esc, " "))
	}
	return strings.Join(lines, "\n") + "\n"
}
