// Package patch reports waiting updates and whether a reboot is needed, from what the package
// manager already knows. See "Patch report" in the agent protocol.
package patch

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/labdex-app/labdex/agent/internal/runner"
)

// Package is one waiting update.
type Package struct {
	Name     string `json:"name"`
	From     string `json:"from"`
	To       string `json:"to"`
	Security bool   `json:"security"`
}

var aptInst = regexp.MustCompile(`^Inst (\S+) (?:\[(\S+)\] )?\((\S+) ([^)]*)\)`)

func lines(s string) []string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = strings.TrimSuffix(s, "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}

// ParseApt reads `apt-get -s dist-upgrade`. A package from a *-security pocket is a security update.
func ParseApt(text string) []Package {
	out := []Package{}
	for _, line := range lines(text) {
		if m := aptInst.FindStringSubmatch(line); m != nil {
			out = append(out, Package{Name: m[1], From: m[2], To: m[3],
				Security: strings.Contains(m[4], "-security") || strings.Contains(m[4], "Debian-Security")})
		}
	}
	return out
}

// rpmName: openssl-libs-1:3.0.7-27.el9.x86_64 → openssl-libs
func rpmName(nevra string) string {
	if strings.Count(nevra, "-") >= 2 {
		i := strings.LastIndex(nevra, "-")
		j := strings.LastIndex(nevra[:i], "-")
		return nevra[:j]
	}
	return nevra
}

// ParseDnf reads `dnf check-update` and `dnf updateinfo list --security`.
func ParseDnf(checkUpdate, security string) []Package {
	sec := map[string]bool{}
	for _, line := range lines(security) {
		if f := strings.Fields(line); len(f) >= 3 {
			sec[rpmName(f[len(f)-1])] = true
		}
	}
	out := []Package{}
	for _, line := range lines(checkUpdate) {
		f := strings.Fields(line)
		if len(f) != 3 || !strings.Contains(f[0], ".") || strings.HasPrefix(line, "Last metadata") ||
			strings.HasPrefix(line, "Obsoleting") || strings.HasPrefix(line, " ") {
			continue
		}
		name := f[0][:strings.LastIndex(f[0], ".")]
		out = append(out, Package{Name: name, To: f[1], Security: sec[name]})
	}
	return out
}

// AptPatchedAt is when apt last upgraded something: the Start-Date of the last entry with an
// Upgrade: line, as ISO 8601 in local time; "" for never.
func AptPatchedAt(log string) string {
	b, err := os.ReadFile(log)
	if err != nil {
		return ""
	}
	when, last := "", ""
	for _, line := range lines(string(b)) {
		if strings.HasPrefix(line, "Start-Date:") {
			when = strings.TrimSpace(strings.SplitN(line, ":", 2)[1])
		} else if strings.HasPrefix(line, "Upgrade:") && when != "" {
			last = when
		}
	}
	if last == "" {
		return ""
	}
	t, err := time.ParseInLocation("2006-01-02  15:04:05", last, time.Local)
	if err != nil {
		return ""
	}
	return t.Format("2006-01-02T15:04:05-07:00")
}

func rpmPatchedAt(r runner.Runner) string {
	code, out, _ := r.Run([]string{"rpm", "-qa", "--qf", "%{INSTALLTIME}\n"}, 600*time.Second)
	var newest int64
	for _, f := range strings.Fields(out) {
		if n, err := strconv.ParseInt(f, 10, 64); err == nil && n > newest {
			newest = n
		}
	}
	if code != 0 || newest == 0 {
		return ""
	}
	return time.Unix(newest, 0).UTC().Format("2006-01-02T15:04:05-07:00")
}

func versionKey(v string) []string {
	return regexp.MustCompile(`[.-]`).Split(v, -1)
}

// lessVersion compares like Python's list comparison of int-or-text parts; ok is false when a
// number meets text in the same place (Python raises TypeError there).
func lessVersion(a, b string) (bool, bool) {
	x, y := versionKey(a), versionKey(b)
	for i := 0; i < len(x) && i < len(y); i++ {
		nx, ex := strconv.Atoi(x[i])
		ny, ey := strconv.Atoi(y[i])
		isNumX, isNumY := ex == nil && isDigits(x[i]), ey == nil && isDigits(y[i])
		switch {
		case isNumX && isNumY:
			if nx != ny {
				return nx < ny, true
			}
		case !isNumX && !isNumY:
			if x[i] != y[i] {
				return x[i] < y[i], true
			}
		default:
			return false, false
		}
	}
	return len(x) < len(y), true
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// NewestKernelDiffers: Proxmox and some Debian setups don't write reboot-required for a new kernel.
func NewestKernelDiffers(running string, boot string) bool {
	paths, _ := filepath.Glob(filepath.Join(boot, "vmlinuz-*"))
	var installed []string
	found := false
	for _, p := range paths {
		v := strings.SplitN(filepath.Base(p), "vmlinuz-", 2)[1]
		installed = append(installed, v)
		if v == running {
			found = true
		}
	}
	if len(installed) == 0 || !found {
		return false
	}
	sort.Strings(installed) // glob order is sorted in Python too; max() keeps the first of equals
	newest := installed[0]
	for _, v := range installed[1:] {
		less, ok := lessVersion(newest, v)
		if !ok {
			return false
		}
		if less {
			newest = v
		}
	}
	return newest != running
}

func osName(r runner.Runner) string {
	rel := readRelease()
	name := rel
	if r.Has("pveversion") {
		code, out, _ := r.Run([]string{"pveversion"}, 600*time.Second)
		if code == 0 && strings.TrimSpace(out) != "" {
			first := strings.Fields(out)[0]
			first = strings.Replace(first, "pve-manager/", "Proxmox VE ", 1)
			name = strings.Split(first, "/")[0]
			if rel != "" {
				name += " (" + rel + ")"
			}
		}
	}
	return name
}

func readRelease() string {
	b, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return ""
	}
	for _, line := range lines(string(b)) {
		if k, v, ok := strings.Cut(line, "="); ok && k == "PRETTY_NAME" {
			return strings.Trim(v, `"`)
		}
	}
	return ""
}

func kernel() string {
	b, _ := os.ReadFile("/proc/sys/kernel/osrelease")
	return strings.TrimSpace(string(b))
}

// Collect makes the report; it never refreshes the package lists.
func Collect(r runner.Runner) map[string]any {
	report := map[string]any{"os": osName(r), "kernel": kernel()}
	packages := []Package{}
	switch {
	case r.Has("apt-get"):
		code, out, _ := r.Run([]string{"apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"}, 600*time.Second)
		if code != 0 {
			report["error"] = "apt-get -s dist-upgrade failed"
		}
		packages = ParseApt(out)
		_, err := os.Stat("/var/run/reboot-required")
		report["reboot"] = err == nil || (r.Has("pveversion") && NewestKernelDiffers(kernel(), "/boot"))
		report["patched_at"] = nullable(AptPatchedAt("/var/log/apt/history.log"))
	case r.Has("dnf") || r.Has("yum"):
		dnf := "yum"
		if r.Has("dnf") {
			dnf = "dnf"
		}
		code, out, _ := r.Run([]string{dnf, "-q", "--cacheonly", "check-update"}, 600*time.Second)
		if code != 0 && code != 100 {
			report["error"] = dnf + " check-update failed"
		}
		_, sec, _ := r.Run([]string{dnf, "-q", "--cacheonly", "updateinfo", "list", "--security", "--available"}, 600*time.Second)
		packages = ParseDnf(out, sec)
		code, _, _ = r.Run([]string{"needs-restarting", "-r"}, 600*time.Second)
		report["reboot"] = code == 1
		report["patched_at"] = nullable(rpmPatchedAt(r))
	default:
		report["error"] = "no apt-get, dnf or yum here"
	}
	report["packages"] = packages
	report["pending"] = len(packages)
	return report
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

var fetchFailed = []string{"Err:", "E: ", "W: Failed to fetch", "W: Some index files failed"}

// AptFetchFailed: apt-get update exits 0 when a mirror can't be reached.
func AptFetchFailed(output string) bool {
	for _, line := range lines(output) {
		for _, p := range fetchFailed {
			if strings.HasPrefix(line, p) {
				return true
			}
		}
	}
	return false
}
