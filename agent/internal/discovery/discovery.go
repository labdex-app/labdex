// Package discovery finds services on the host, read-only: what listens, which process runs it,
// which systemd unit and which container it belongs to. See "Discovery" in the agent protocol.
package discovery

import (
	"encoding/json"
	"math"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/labdex-app/labdex/agent/internal/runner"
)

const (
	MaxProcesses = 500
	MaxCmdline   = 1000
	Mask         = "***"
	clockTicks   = 100 // USER_HZ, the same on every Linux architecture that matters
)

// a flag or variable whose value is a secret: --password x, --token=x, DB_PASS=x, apikey: x
const secretWord = `[\w.-]*(?:pass(?:wd|word|phrase)?|secret|token|api[-_]?key|access[-_]?key|private[-_]?key|credentials?|auth)[\w.-]*`

var (
	secretFlag   = regexp.MustCompile(`(?i)^(--?` + secretWord + `)(=.*)?$`)
	secretAssign = regexp.MustCompile(`(?i)^(` + secretWord + `)([=:])(.+)$`)
	urlUserinfo  = regexp.MustCompile(`(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]*:)[^@/\s]+@`)
)

// Redact masks secrets in a command line: the value after a secret flag, in a secret assignment,
// in a URL's user info.
func Redact(argv []string) []string {
	out := make([]string, 0, len(argv))
	hideNext := false
	for _, a := range argv {
		if hideNext {
			out = append(out, Mask)
			hideNext = false
			continue
		}
		if m := secretFlag.FindStringSubmatchIndex(a); m != nil {
			if m[4] < 0 { // no =value
				out = append(out, a)
				hideNext = true
			} else {
				out = append(out, a[m[2]:m[3]]+"="+Mask)
			}
			continue
		}
		if m := secretAssign.FindStringSubmatch(a); m != nil {
			out = append(out, m[1]+m[2]+Mask)
			continue
		}
		out = append(out, urlUserinfo.ReplaceAllString(a, "${1}"+Mask+"@"))
	}
	return out
}

// ---------------------------------------------------------------------------- listeners

// PID is a process behind a socket, as ss names it.
type PID struct {
	Name string `json:"name"`
	PID  int    `json:"pid"`
}

// ContainerRef is the container that publishes a listener's port.
type ContainerRef struct {
	Engine string `json:"engine"`
	Name   string `json:"name"`
	Image  string `json:"image"`
}

// Listener is one listening socket.
type Listener struct {
	Proto     string        `json:"proto"`
	Address   string        `json:"address"`
	Port      int           `json:"port"`
	PIDs      []PID         `json:"pids"`
	Units     *[]string     `json:"units,omitempty"`
	Container *ContainerRef `json:"container,omitempty"`
}

var ssUsers = regexp.MustCompile(`\("([^"]*)",pid=(\d+),fd=\d+\)`)

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

func splitAddr(text string) (string, int, bool) {
	addr, port := "", text
	if i := strings.LastIndex(text, ":"); i >= 0 {
		addr, port = text[:i], text[i+1:]
	}
	if !isDigits(port) {
		return "", 0, false
	}
	addr = strings.Trim(addr, "[]")
	addr, _, _ = strings.Cut(addr, "%")
	if addr == "*" || addr == "" {
		addr = "0.0.0.0"
	}
	n, err := strconv.Atoi(port)
	return addr, n, err == nil
}

// ParseSS reads `ss -Htulpn` (or -Htuln).
func ParseSS(text string) []Listener {
	out := []Listener{}
	seen := map[[3]string]bool{}
	for _, line := range splitLines(text) {
		f := strings.Fields(line)
		if len(f) < 5 || (f[0] != "tcp" && f[0] != "udp") || (f[1] != "LISTEN" && f[1] != "UNCONN") {
			continue
		}
		addr, port, ok := splitAddr(f[4])
		if !ok {
			continue
		}
		key := [3]string{f[0], addr, strconv.Itoa(port)}
		if seen[key] { // ss lists a socket once per process sharing it; the users column has them all
			continue
		}
		seen[key] = true
		byPID := map[int]PID{}
		rest := ""
		if len(f) > 6 {
			rest = strings.Join(f[6:], " ")
		}
		for _, m := range ssUsers.FindAllStringSubmatch(rest, -1) {
			pid, _ := strconv.Atoi(m[2])
			byPID[pid] = PID{Name: m[1], PID: pid}
		}
		pids := make([]PID, 0, len(byPID))
		for _, p := range byPID {
			pids = append(pids, p)
		}
		sort.Slice(pids, func(i, j int) bool { return pids[i].PID < pids[j].PID })
		out = append(out, Listener{Proto: f[0], Address: addr, Port: port, PIDs: pids})
	}
	sort.SliceStable(out, func(i, j int) bool {
		a, b := out[i], out[j]
		if a.Port != b.Port {
			return a.Port < b.Port
		}
		if a.Proto != b.Proto {
			return a.Proto < b.Proto
		}
		return a.Address < b.Address
	})
	return out
}

// splitLines is Python's str.splitlines for \n and \r\n.
func splitLines(text string) []string {
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.TrimSuffix(text, "\n")
	if text == "" {
		return nil
	}
	return strings.Split(text, "\n")
}

func clip(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

func listeners(r runner.Runner) ([]Listener, []string) {
	var notes []string
	code, out, errText := r.Privileged("ss", 60*time.Second)
	if code != 0 {
		switch {
		case strings.Contains(errText, "no new privileges"):
			notes = append(notes, "no processes for listening ports: sudo can't run under NoNewPrivileges (check the service's hardening)")
		case strings.Contains(errText, "sudo") || code == 1:
			notes = append(notes, "no processes for listening ports: the sudo rule for ss is missing")
		default:
			notes = append(notes, "ss with processes failed: "+clip(strings.TrimSpace(errText), 200))
		}
		code, out, errText = r.Run([]string{"ss", "-Htuln"}, 60*time.Second)
		if code != 0 {
			return []Listener{}, append(notes, "ss failed: "+clip(strings.TrimSpace(errText), 200))
		}
	}
	return ParseSS(out), notes
}

// ---------------------------------------------------------------------------- processes

// Parent names a process's parent.
type Parent struct {
	PID  int    `json:"pid"`
	Name string `json:"name"`
}

// Process is one process behind a listener or a unit.
type Process struct {
	PID     int     `json:"pid"`
	PPID    int     `json:"ppid"`
	Name    string  `json:"name"`
	User    string  `json:"user"`
	UID     int     `json:"uid"`
	Cmdline string  `json:"cmdline"`
	Unit    string  `json:"unit"`
	Started *int64  `json:"started,omitempty"`
	Parent  *Parent `json:"parent,omitempty"`
}

var unitRe = regexp.MustCompile(`/([^/]+\.(?:service|scope))(?:/|$)`)

// UnitOfCgroup is the innermost systemd service or scope in /proc/<pid>/cgroup, "" for none.
func UnitOfCgroup(text string) string {
	for _, line := range splitLines(text) {
		parts := strings.SplitN(line, ":", 3)
		path := parts[len(parts)-1]
		if m := unitRe.FindAllStringSubmatch(path, -1); len(m) > 0 {
			return m[len(m)-1][1]
		}
	}
	return ""
}

func readFile(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.ToValidUTF8(string(b), "�")
}

// Users maps uids to names from /etc/passwd.
func Users() map[int]string {
	out := map[int]string{}
	for _, line := range splitLines(readFile("/etc/passwd")) {
		f := strings.Split(line, ":")
		if len(f) >= 3 {
			if uid, err := strconv.Atoi(f[2]); err == nil {
				if _, dup := out[uid]; !dup {
					out[uid] = f[0]
				}
			}
		}
	}
	return out
}

func process(pid int, users map[int]string, proc string, boot *float64) *Process {
	status := readFile(proc + "/" + strconv.Itoa(pid) + "/status")
	if status == "" {
		return nil
	}
	fields := map[string]string{}
	for _, line := range splitLines(status) {
		if k, v, ok := strings.Cut(line, ":"); ok {
			fields[k] = v
		}
	}
	uid := -1
	if u := strings.Fields(fields["Uid"]); len(u) > 0 {
		uid, _ = strconv.Atoi(u[0])
	}
	var argv []string
	for _, a := range strings.Split(readFile(proc+"/"+strconv.Itoa(pid)+"/cmdline"), "\x00") {
		if a != "" {
			argv = append(argv, a)
		}
	}
	name := strings.TrimSpace(fields["Name"])
	cmd := clip(strings.Join(Redact(argv), " "), MaxCmdline)
	if cmd == "" {
		cmd = "[" + name + "]"
	}
	ppid, _ := strconv.Atoi(strings.TrimSpace(fields["PPid"]))
	user, ok := users[uid]
	if !ok {
		user = strconv.Itoa(uid)
	}
	p := &Process{PID: pid, PPID: ppid, Name: name, User: user, UID: uid, Cmdline: cmd,
		Unit: UnitOfCgroup(readFile(proc + "/" + strconv.Itoa(pid) + "/cgroup"))}
	if stat := readFile(proc + "/" + strconv.Itoa(pid) + "/stat"); stat != "" && boot != nil {
		if i := strings.LastIndex(stat, ")"); i >= 0 {
			f := strings.Fields(stat[i+1:])
			if len(f) > 19 {
				if ticks, err := strconv.ParseInt(f[19], 10, 64); err == nil {
					s := int64(math.RoundToEven(*boot + float64(ticks)/clockTicks))
					p.Started = &s
				}
			}
		}
	}
	return p
}

// Processes reads the given processes and their parents (one level up).
func Processes(pids map[int]bool, proc string) []*Process {
	users := Users()
	var boot *float64
	if f := strings.Fields(readFile(proc + "/uptime")); len(f) > 0 {
		if up, err := strconv.ParseFloat(f[0], 64); err == nil {
			b := float64(time.Now().UnixNano())/1e9 - up
			boot = &b
		}
	}
	sorted := make([]int, 0, len(pids))
	for p := range pids {
		sorted = append(sorted, p)
	}
	sort.Ints(sorted)
	if len(sorted) > MaxProcesses {
		sorted = sorted[:MaxProcesses]
	}
	byPID := map[int]*Process{}
	var order []int
	for _, pid := range sorted {
		if p := process(pid, users, proc, boot); p != nil {
			byPID[pid] = p
			order = append(order, pid)
		}
	}
	for _, pid := range append([]int(nil), order...) {
		p := byPID[pid]
		if _, have := byPID[p.PPID]; p.PPID > 1 && !have && len(byPID) < MaxProcesses {
			if parent := process(p.PPID, users, proc, boot); parent != nil {
				byPID[p.PPID] = parent
			}
		}
	}
	out := make([]*Process, 0, len(byPID))
	for _, p := range byPID {
		name := ""
		if parent, ok := byPID[p.PPID]; ok {
			name = parent.Name
		} else if p.PPID == 1 {
			name = "systemd"
		}
		p.Parent = &Parent{PID: p.PPID, Name: name}
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].PID < out[j].PID })
	return out
}

// ---------------------------------------------------------------------------- systemd

// Unit is a running systemd service.
type Unit struct {
	Unit        string `json:"unit"`
	Description string `json:"description"`
	PID         *int   `json:"pid"`
	User        string `json:"user"`
	Path        string `json:"path"`
	Since       string `json:"since"`
}

// ParseShow reads `systemctl show -p Id,Description,MainPID,User,FragmentPath,ActiveEnterTimestamp …`.
func ParseShow(text string) []Unit {
	out := []Unit{}
	for _, block := range strings.Split(strings.TrimSpace(text), "\n\n") {
		f := map[string]string{}
		for _, line := range splitLines(block) {
			if k, v, ok := strings.Cut(line, "="); ok {
				f[k] = v
			}
		}
		if f["Id"] == "" {
			continue
		}
		u := Unit{Unit: f["Id"], Description: f["Description"], User: f["User"], Path: f["FragmentPath"], Since: f["ActiveEnterTimestamp"]}
		if pid, _ := strconv.Atoi(f["MainPID"]); pid != 0 {
			u.PID = &pid
		}
		if u.User == "" {
			u.User = "root"
		}
		out = append(out, u)
	}
	return out
}

func units(r runner.Runner) ([]Unit, []string) {
	if !r.Has("systemctl") {
		return []Unit{}, []string{"no systemd here"}
	}
	code, out, errText := r.Run([]string{"systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--plain", "--no-pager"}, 60*time.Second)
	if code != 0 {
		return []Unit{}, []string{"systemctl list-units failed: " + clip(strings.TrimSpace(errText), 200)}
	}
	var names []string
	for _, line := range splitLines(out) {
		if f := strings.Fields(line); len(f) > 0 {
			names = append(names, f[0])
		}
	}
	if len(names) == 0 {
		return []Unit{}, nil
	}
	argv := append([]string{"systemctl", "show", "--no-pager", "-p", "Id,Description,MainPID,User,FragmentPath,ActiveEnterTimestamp", "--"}, names...)
	code, out, errText = r.Run(argv, 60*time.Second)
	if code != 0 {
		return []Unit{}, []string{"systemctl show failed: " + clip(strings.TrimSpace(errText), 200)}
	}
	return ParseShow(out), nil
}

// ---------------------------------------------------------------------------- containers

// Port is a port a container publishes.
type Port struct {
	Address       string `json:"address"`
	Port          int    `json:"port"`
	ContainerPort int    `json:"container_port"`
	Proto         string `json:"proto"`
}

// Container is a running Docker or Podman container.
type Container struct {
	Engine         string `json:"engine"`
	ID             string `json:"id"`
	Name           string `json:"name"`
	Image          string `json:"image"`
	Status         string `json:"status"`
	ComposeProject string `json:"compose_project"`
	Ports          []Port `json:"ports"`
	PID            *any   `json:"pid,omitempty"` // podman only; null when it has none
}

var portRe = regexp.MustCompile(`(?:(\[?[0-9a-fA-F.:]*\]?):)?(\d+)(?:-(\d+))?->(\d+)(?:-\d+)?/(tcp|udp)`)

// ParseDockerPorts reads '0.0.0.0:8080->80/tcp, [::]:8080->80/tcp'.
func ParseDockerPorts(text string) []Port {
	out := []Port{}
	for _, m := range portRe.FindAllStringSubmatch(text, -1) {
		addr, start, end, cport, proto := m[1], m[2], m[3], m[4], m[5]
		s, _ := strconv.Atoi(start)
		e := s
		if end != "" {
			e, _ = strconv.Atoi(end)
		}
		c, _ := strconv.Atoi(cport)
		a := strings.Trim(addr, "[]")
		if a == "" {
			a = "0.0.0.0"
		}
		for i, p := 0, s; p <= e; i, p = i+1, p+1 {
			out = append(out, Port{Address: a, Port: p, ContainerPort: c + i, Proto: proto})
		}
	}
	return out
}

func str(v any) string {
	s, _ := v.(string)
	return s
}

func label(labels, key string) string {
	for _, kv := range strings.Split(labels, ",") {
		k, v, _ := strings.Cut(kv, "=")
		if k == key {
			return v
		}
	}
	return ""
}

// ParseDocker reads `docker ps --format {{json .}}`, one object per line.
func ParseDocker(text string) []Container {
	out := []Container{}
	for _, line := range splitLines(text) {
		var c map[string]any
		if json.Unmarshal([]byte(line), &c) != nil || c == nil {
			continue
		}
		out = append(out, Container{Engine: "docker", ID: clip(str(c["ID"]), 12), Name: str(c["Names"]), Image: str(c["Image"]),
			Status: str(c["Status"]), ComposeProject: label(str(c["Labels"]), "com.docker.compose.project"),
			Ports: ParseDockerPorts(str(c["Ports"]))})
	}
	return out
}

func num(v any, def int) int {
	switch x := v.(type) {
	case float64:
		return int(x)
	case string:
		if n, err := strconv.Atoi(x); err == nil {
			return n
		}
	}
	return def
}

// ParsePodman reads `podman ps --format json`, one array.
func ParsePodman(text string) []Container {
	if text == "" {
		text = "[]"
	}
	var rows []map[string]any
	if json.Unmarshal([]byte(text), &rows) != nil {
		return []Container{}
	}
	out := []Container{}
	for _, c := range rows {
		ports := []Port{}
		list, _ := c["Ports"].([]any)
		for _, raw := range list {
			p, _ := raw.(map[string]any)
			if p == nil {
				continue
			}
			n := num(p["range"], 1)
			if n == 0 {
				n = 1
			}
			addr := str(p["host_ip"])
			if addr == "" {
				addr = "0.0.0.0"
			}
			proto := str(p["protocol"])
			if proto == "" {
				proto = "tcp"
			}
			for i := 0; i < n; i++ {
				ports = append(ports, Port{Address: addr, Port: num(p["host_port"], 0) + i, ContainerPort: num(p["container_port"], 0) + i, Proto: proto})
			}
		}
		name := ""
		if names, _ := c["Names"].([]any); len(names) > 0 {
			name = str(names[0])
		}
		status := str(c["Status"])
		if status == "" {
			status = str(c["State"])
		}
		labels, _ := c["Labels"].(map[string]any)
		id := ""
		switch x := c["Id"].(type) {
		case string:
			id = x
		case nil:
		default:
			b, _ := json.Marshal(x)
			id = string(b)
		}
		pid := c["Pid"]
		out = append(out, Container{Engine: "podman", ID: clip(id, 12), Name: name, Image: str(c["Image"]), Status: status,
			ComposeProject: str(labels["com.docker.compose.project"]), Ports: ports, PID: &pid})
	}
	return out
}

func containers(r runner.Runner) ([]Container, []string) {
	out, notes := []Container{}, []string(nil)
	for _, e := range []struct {
		engine, name string
		parse        func(string) []Container
	}{{"docker", "docker_ps", ParseDocker}, {"podman", "podman_ps", ParsePodman}} {
		if !r.Has(e.engine) {
			continue
		}
		code, text, errText := r.Privileged(e.name, 60*time.Second)
		if code != 0 {
			notes = append(notes, e.engine+" ps failed (is the sudo rule there?): "+clip(strings.TrimSpace(errText), 200))
			continue
		}
		out = append(out, e.parse(text)...)
	}
	return out, notes
}

// ---------------------------------------------------------------------------- together

// Result is what discovery.services answers.
type Result struct {
	CollectedAt int64       `json:"collected_at"`
	Listeners   []Listener  `json:"listeners"`
	Units       []Unit      `json:"units"`
	Containers  []Container `json:"containers"`
	Processes   []*Process  `json:"processes"`
	Notes       []string    `json:"notes"`
}

// Collect runs the whole discovery. proc is /proc except in tests.
func Collect(r runner.Runner, proc string) Result {
	socks, notes := listeners(r)
	svc, n2 := units(r)
	ctrs, n3 := containers(r)
	pids := map[int]bool{}
	for _, s := range socks {
		for _, p := range s.PIDs {
			pids[p.PID] = true
		}
	}
	for _, u := range svc {
		if u.PID != nil {
			pids[*u.PID] = true
		}
	}
	procs := Processes(pids, proc)
	byPID := map[int]*Process{}
	for _, p := range procs {
		byPID[p.PID] = p
	}
	type pk struct {
		proto string
		port  int
	}
	published := map[pk]*Container{}
	for i := range ctrs {
		for _, p := range ctrs[i].Ports {
			published[pk{p.Proto, p.Port}] = &ctrs[i]
		}
	}
	for i := range socks {
		set := map[string]bool{}
		for _, p := range socks[i].PIDs {
			if pr, ok := byPID[p.PID]; ok && pr.Unit != "" {
				set[pr.Unit] = true
			}
		}
		here := make([]string, 0, len(set))
		for u := range set {
			here = append(here, u)
		}
		sort.Strings(here)
		socks[i].Units = &here
		if c, ok := published[pk{socks[i].Proto, socks[i].Port}]; ok {
			socks[i].Container = &ContainerRef{Engine: c.Engine, Name: c.Name, Image: c.Image}
		}
	}
	all := append(append(append([]string{}, notes...), n2...), n3...)
	return Result{CollectedAt: int64(math.RoundToEven(float64(time.Now().UnixNano()) / 1e9)), Listeners: socks, Units: svc,
		Containers: ctrs, Processes: procs, Notes: all}
}
