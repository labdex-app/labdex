package discovery

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

type conformance struct {
	Redact []struct {
		Argv []string `json:"argv"`
		Out  []string `json:"out"`
	} `json:"redact"`
	SS struct {
		Text string `json:"text"`
		Out  any    `json:"out"`
	} `json:"ss"`
	Show struct {
		Text string `json:"text"`
		Out  any    `json:"out"`
	} `json:"systemctl_show"`
	Docker struct {
		Text string `json:"text"`
		Out  any    `json:"out"`
	} `json:"docker"`
	Podman struct {
		Text string `json:"text"`
		Out  any    `json:"out"`
	} `json:"podman"`
	Cgroup []struct {
		Text string `json:"text"`
		Unit string `json:"unit"`
	} `json:"cgroup_unit"`
}

func load(t *testing.T) conformance {
	t.Helper()
	raw, err := os.ReadFile("../../testdata/conformance.json")
	if err != nil {
		t.Fatal(err)
	}
	var c conformance
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	return c
}

// same compares a Go value with the expected JSON by their JSON forms.
func same(t *testing.T, what string, got, want any) {
	t.Helper()
	b, _ := json.Marshal(got)
	var g any
	_ = json.Unmarshal(b, &g)
	if !reflect.DeepEqual(g, want) {
		w, _ := json.Marshal(want)
		t.Errorf("%s:\n got %s\nwant %s", what, b, w)
	}
}

func TestConformance(t *testing.T) {
	c := load(t)
	for _, r := range c.Redact {
		if got := Redact(r.Argv); !reflect.DeepEqual(got, r.Out) {
			t.Errorf("Redact(%q) = %q, want %q", r.Argv, got, r.Out)
		}
	}
	same(t, "ss", ParseSS(c.SS.Text), c.SS.Out)
	same(t, "systemctl show", ParseShow(c.Show.Text), c.Show.Out)
	same(t, "docker", ParseDocker(c.Docker.Text), c.Docker.Out)
	same(t, "podman", ParsePodman(c.Podman.Text), c.Podman.Out)
	for _, g := range c.Cgroup {
		if got := UnitOfCgroup(g.Text); got != g.Unit {
			t.Errorf("UnitOfCgroup(%q) = %q, want %q", g.Text, got, g.Unit)
		}
	}
}

type fake struct {
	root     bool
	programs map[string]bool
	priv     map[string][3]string // name -> code? no: out, err, "fail"
	run      map[string]string
}

func (f *fake) Root() bool        { return f.root }
func (f *fake) Has(p string) bool { return f.programs[p] }
func (f *fake) Run(argv []string, _ time.Duration) (int, string, string) {
	if out, ok := f.run[strings.Join(argv[:2], " ")]; ok {
		return 0, out, ""
	}
	return 1, "", "no"
}
func (f *fake) PrivilegedInput(name string, _ []byte, t time.Duration) (int, string, string) {
	return f.Privileged(name, t)
}

func (f *fake) Privileged(name string, _ time.Duration) (int, string, string) {
	if v, ok := f.priv[name]; ok {
		if v[2] != "" {
			return 1, "", v[2]
		}
		return 0, v[0], ""
	}
	return 1, "", "sudo: a password is required"
}

func writeProc(t *testing.T, dir string, pid int, name, ppid, uid, cmdline, cgroup string) {
	t.Helper()
	d := filepath.Join(dir, itoa(pid))
	if err := os.MkdirAll(d, 0o755); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(d, "status"), []byte("Name:\t"+name+"\nPPid:\t"+ppid+"\nUid:\t"+uid+"\t"+uid+"\t"+uid+"\t"+uid+"\n"), 0o644)
	_ = os.WriteFile(filepath.Join(d, "cmdline"), []byte(cmdline), 0o644)
	_ = os.WriteFile(filepath.Join(d, "cgroup"), []byte(cgroup), 0o644)
	_ = os.WriteFile(filepath.Join(d, "stat"), []byte(itoa(pid)+" (x y) S "+ppid+" 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 5000 0"), 0o644)
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func TestCollect(t *testing.T) {
	proc := t.TempDir()
	_ = os.WriteFile(filepath.Join(proc, "uptime"), []byte("1000.00 2000.00\n"), 0o644)
	writeProc(t, proc, 812, "sshd", "1", "0", "/usr/sbin/sshd\x00-D\x00", "0::/system.slice/ssh.service\n")
	writeProc(t, proc, 1500, "docker-proxy", "1400", "0", "docker-proxy\x00--password\x00x\x00", "0::/system.slice/docker.service\n")
	writeProc(t, proc, 1400, "dockerd", "1", "0", "", "0::/system.slice/docker.service\n")
	f := &fake{programs: map[string]bool{"systemctl": true, "docker": true},
		priv: map[string][3]string{
			"ss": {"tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:((\"sshd\",pid=812,fd=3))\n" +
				"tcp LISTEN 0 4096 0.0.0.0:5380 0.0.0.0:* users:((\"docker-proxy\",pid=1500,fd=4))\n", "", ""},
			"docker_ps": {`{"ID":"0123456789abcdef","Names":"dns","Image":"technitium/dns-server","Status":"Up","Labels":"","Ports":"0.0.0.0:5380->5380/tcp"}` + "\n", "", ""},
		},
		run: map[string]string{
			"systemctl list-units": "ssh.service loaded active running SSH\n",
			"systemctl show":       "Id=ssh.service\nMainPID=812\n",
		}}
	r := Collect(f, proc)
	if len(r.Notes) != 0 {
		t.Fatalf("notes: %v", r.Notes)
	}
	if len(r.Listeners) != 2 || (*r.Listeners[0].Units)[0] != "ssh.service" || r.Listeners[1].Container == nil || r.Listeners[1].Container.Name != "dns" {
		t.Fatalf("listeners: %+v", r.Listeners)
	}
	if len(r.Processes) != 3 {
		t.Fatalf("processes: %d", len(r.Processes))
	}
	dp := r.Processes[2]
	if dp.PID != 1500 || dp.Cmdline != "docker-proxy --password ***" || dp.Parent.Name != "dockerd" || dp.Started == nil {
		t.Fatalf("docker-proxy: %+v %+v", dp, dp.Parent)
	}
	if r.Processes[1].Cmdline != "[dockerd]" || r.Processes[0].Parent.Name != "systemd" {
		t.Fatalf("dockerd/sshd: %+v %+v", r.Processes[1], r.Processes[0].Parent)
	}

	// without the sudo rule: ss without processes, and a note
	f.priv = map[string][3]string{}
	f.run["ss -Htuln"] = "tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n"
	r = Collect(f, proc)
	if len(r.Listeners) != 1 || len(r.Notes) != 2 || !strings.Contains(r.Notes[0], "sudo rule for ss") || !strings.Contains(r.Notes[1], "docker ps failed") {
		t.Fatalf("without sudo: %+v %v", r.Listeners, r.Notes)
	}
}
