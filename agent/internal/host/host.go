// Package host reads facts (now and then) and metrics (every heartbeat) about this host, from
// /proc, /etc/os-release and statfs: no commands, no root.
package host

import (
	"math"
	"net"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Proc is where /proc is; tests point it elsewhere.
var Proc = "/proc"

func read(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.ToValidUTF8(string(b), "�")
}

func lines(s string) []string {
	s = strings.TrimSuffix(s, "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}

// OSRelease reads /etc/os-release.
func OSRelease(path string) map[string]string {
	out := map[string]string{}
	for _, line := range lines(read(path)) {
		if k, v, ok := strings.Cut(line, "="); ok && !strings.HasPrefix(line, "#") {
			out[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `"`)
		}
	}
	return out
}

// Meminfo reads /proc/meminfo in bytes.
func Meminfo(proc string) map[string]int64 {
	out := map[string]int64{}
	for _, line := range lines(read(proc + "/meminfo")) {
		f := strings.Fields(line)
		if len(f) >= 2 {
			if n, err := strconv.ParseInt(f[1], 10, 64); err == nil {
				out[strings.TrimSuffix(f[0], ":")] = n * 1024
			}
		}
	}
	return out
}

// CPUTimes is (busy, total) jiffies since boot from the first line of /proc/stat.
func CPUTimes(proc string) (int64, int64) {
	for _, line := range lines(read(proc + "/stat")) {
		if strings.HasPrefix(line, "cpu ") {
			var v []int64
			for _, x := range strings.Fields(line)[1:] {
				n, _ := strconv.ParseInt(x, 10, 64)
				v = append(v, n)
			}
			var total int64
			for _, n := range v {
				total += n
			}
			idle := int64(0)
			if len(v) > 3 {
				idle = v[3]
			}
			if len(v) > 4 {
				idle += v[4] // iowait
			}
			return total - idle, total
		}
	}
	return 0, 0
}

// Uptime is the seconds since boot.
func Uptime(proc string) float64 {
	f := strings.Fields(read(proc + "/uptime"))
	if len(f) == 0 {
		return 0
	}
	v, _ := strconv.ParseFloat(f[0], 64)
	return v
}

var realFS = map[string]bool{"ext2": true, "ext3": true, "ext4": true, "xfs": true, "btrfs": true, "zfs": true, "vfat": true,
	"f2fs": true, "jfs": true, "reiserfs": true, "bcachefs": true}

// Mounts lists mount points of real file systems (no tmpfs, overlay, proc …).
func Mounts(proc string) []string {
	var out []string
	seen := map[string]bool{}
	for _, line := range lines(read(proc + "/mounts")) {
		f := strings.Fields(line)
		if len(f) < 3 {
			continue
		}
		path := strings.ReplaceAll(f[1], `\040`, " ")
		if st, err := os.Stat(path); err == nil && st.IsDir() && realFS[f[2]] && !seen[path] {
			seen[path] = true
			out = append(out, path)
		}
	}
	if len(out) == 0 {
		return []string{"/"}
	}
	return out
}

// Disk is one file system's size.
type Disk struct {
	Mount string `json:"mount"`
	Total int64  `json:"total"`
	Used  int64  `json:"used"`
	Free  int64  `json:"free"`
}

// DiskOf reads a mount point's size; ok is false when it can't.
func DiskOf(path string) (Disk, bool) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(path, &st); err != nil {
		return Disk{}, false
	}
	fr := int64(st.Frsize)
	if fr == 0 {
		fr = int64(st.Bsize)
	}
	total := int64(st.Blocks) * fr
	return Disk{Mount: path, Total: total, Used: total - int64(st.Bfree)*fr, Free: int64(st.Bavail) * fr}, true
}

// NetBytes sums received and sent bytes over every interface but lo.
func NetBytes(proc string) (int64, int64) {
	var rx, tx int64
	ls := lines(read(proc + "/net/dev"))
	if len(ls) > 2 {
		for _, line := range ls[2:] {
			name, rest, _ := strings.Cut(line, ":")
			v := strings.Fields(rest)
			if strings.TrimSpace(name) != "lo" && len(v) >= 9 {
				r, _ := strconv.ParseInt(v[0], 10, 64)
				t, _ := strconv.ParseInt(v[8], 10, 64)
				rx, tx = rx+r, tx+t
			}
		}
	}
	return rx, tx
}

func uname() (release, machine string) {
	var u syscall.Utsname
	if syscall.Uname(&u) != nil {
		return "", ""
	}
	conv := func(a [65]int8) string {
		b := make([]byte, 0, len(a))
		for _, c := range a {
			if c == 0 {
				break
			}
			b = append(b, byte(c))
		}
		return string(b)
	}
	return conv(u.Release), conv(u.Machine)
}

// Kernel is `uname -r`.
func Kernel() string { r, _ := uname(); return r }

func fqdn(hostname string) string {
	addrs, err := net.LookupHost(hostname)
	if err != nil || len(addrs) == 0 {
		return hostname
	}
	for _, a := range addrs {
		names, err := net.LookupAddr(a)
		if err != nil {
			continue
		}
		for _, n := range names {
			n = strings.TrimSuffix(n, ".")
			if strings.Contains(n, ".") {
				return n
			}
		}
	}
	return hostname
}

// UserName is the name of the user the agent runs as.
func UserName() string {
	if u, err := user.Current(); err == nil {
		return u.Username
	}
	return strconv.Itoa(os.Geteuid())
}

// Facts is what the agent tells the hub about the host in hello and the facts job.
func Facts() map[string]any {
	rel := OSRelease("/etc/os-release")
	mem := Meminfo(Proc)
	hostname, _ := os.Hostname()
	release, machine := uname()
	osName := rel["PRETTY_NAME"]
	if osName == "" {
		osName = "Linux"
	}
	disks := []Disk{}
	for _, m := range Mounts(Proc) {
		if d, ok := DiskOf(m); ok {
			disks = append(disks, d)
		}
	}
	return map[string]any{
		"hostname":      hostname,
		"fqdn":          fqdn(hostname),
		"os":            osName,
		"os_id":         rel["ID"],
		"os_version":    rel["VERSION_ID"],
		"kernel":        release,
		"arch":          machine,
		"cpus":          numCPU(),
		"memory":        mem["MemTotal"],
		"disks":         disks,
		"agent":         "go",
		"user":          UserName(),
		"root":          os.Geteuid() == 0,
		"ssh_host_keys": SSHHostKeys("/etc/ssh"),
		"boot_time":     int64(math.RoundToEven(float64(time.Now().UnixNano())/1e9 - Uptime(Proc))),
	}
}

// Metrics gives the numbers for each heartbeat. CPU use is over the time since the last call.
type Metrics struct {
	mu          sync.Mutex
	proc        string
	busy, total int64
}

// NewMetrics starts counting CPU use from now.
func NewMetrics(proc string) *Metrics {
	m := &Metrics{proc: proc}
	m.busy, m.total = CPUTimes(proc)
	return m
}

func round(v float64, places int) float64 {
	p := math.Pow(10, float64(places))
	return math.RoundToEven(v*p) / p
}

// Collect reads the metrics.
func (m *Metrics) Collect() map[string]any {
	m.mu.Lock()
	busy, total := CPUTimes(m.proc)
	b0, t0 := m.busy, m.total
	m.busy, m.total = busy, total
	m.mu.Unlock()
	cpu := 0.0
	if total > t0 {
		cpu = round(100*float64(busy-b0)/float64(total-t0), 1)
	}
	load := loadavg(m.proc)
	mem := Meminfo(m.proc)
	root, _ := DiskOf("/")
	rx, tx := NetBytes(m.proc)
	return map[string]any{
		"cpu_percent": cpu,
		"load1":       round(load[0], 2), "load5": round(load[1], 2), "load15": round(load[2], 2),
		"mem_total": mem["MemTotal"], "mem_available": mem["MemAvailable"],
		"swap_total": mem["SwapTotal"], "swap_free": mem["SwapFree"],
		"root_total": root.Total, "root_used": root.Used,
		"uptime":       int64(math.RoundToEven(Uptime(m.proc))),
		"net_rx_bytes": rx, "net_tx_bytes": tx,
	}
}

func loadavg(proc string) [3]float64 {
	var out [3]float64
	f := strings.Fields(read(proc + "/loadavg"))
	for i := 0; i < 3 && i < len(f); i++ {
		out[i], _ = strconv.ParseFloat(f[i], 64)
	}
	return out
}

func numCPU() int { return runtime.NumCPU() }

// SSHHostKeys are sshd's public host keys ("<type> <base64>", sorted, comments dropped), so labdex
// can pin them for Ansible runs over the link instead of trusting them on first use.
func SSHHostKeys(dir string) []string {
	out := []string{}
	paths, _ := filepath.Glob(filepath.Join(dir, "ssh_host_*_key.pub"))
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		f := strings.Fields(string(b))
		if len(f) >= 2 && (strings.HasPrefix(f[0], "ssh-") || strings.HasPrefix(f[0], "ecdsa-")) {
			out = append(out, f[0]+" "+f[1])
		}
	}
	sort.Strings(out)
	return out
}
