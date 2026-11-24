package patch

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestConformance(t *testing.T) {
	raw, err := os.ReadFile("../../testdata/conformance.json")
	if err != nil {
		t.Fatal(err)
	}
	var c struct {
		Apt struct {
			Text string `json:"text"`
			Out  any    `json:"out"`
		} `json:"apt"`
		Dnf struct {
			Text     string `json:"text"`
			Security string `json:"security"`
			Out      any    `json:"out"`
		} `json:"dnf"`
		Fetch []struct {
			Text   string `json:"text"`
			Failed bool   `json:"failed"`
		} `json:"apt_fetch_failed"`
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	same := func(what string, got, want any) {
		b, _ := json.Marshal(got)
		var g any
		_ = json.Unmarshal(b, &g)
		if !reflect.DeepEqual(g, want) {
			w, _ := json.Marshal(want)
			t.Errorf("%s:\n got %s\nwant %s", what, b, w)
		}
	}
	same("apt", ParseApt(c.Apt.Text), c.Apt.Out)
	same("dnf", ParseDnf(c.Dnf.Text, c.Dnf.Security), c.Dnf.Out)
	for _, f := range c.Fetch {
		if AptFetchFailed(f.Text) != f.Failed {
			t.Errorf("AptFetchFailed(%q) != %v", f.Text, f.Failed)
		}
	}
}

func TestAptPatchedAt(t *testing.T) {
	log := filepath.Join(t.TempDir(), "history.log")
	_ = os.WriteFile(log, []byte("Start-Date: 2026-11-01  10:00:00\nInstall: x\nEnd-Date: x\n\n"+
		"Start-Date: 2026-11-20  03:12:44\nUpgrade: openssl (1, 2)\n\nStart-Date: 2026-11-21  09:00:00\nRemove: y\n"), 0o644)
	got := AptPatchedAt(log)
	want := time.Date(2026, 11, 20, 3, 12, 44, 0, time.Local).Format("2006-01-02T15:04:05-07:00")
	if got != want {
		t.Fatalf("%q, want %q", got, want)
	}
	if AptPatchedAt(filepath.Join(t.TempDir(), "none")) != "" {
		t.Fatal("no log: no time")
	}
}

func TestNewestKernelDiffers(t *testing.T) {
	boot := t.TempDir()
	for _, k := range []string{"6.8.12-4-pve", "6.8.12-10-pve", "6.5.13-1-pve"} {
		_ = os.WriteFile(filepath.Join(boot, "vmlinuz-"+k), nil, 0o644)
	}
	if !NewestKernelDiffers("6.8.12-4-pve", boot) {
		t.Fatal("6.8.12-10 is newer than the running 6.8.12-4")
	}
	if NewestKernelDiffers("6.8.12-10-pve", boot) {
		t.Fatal("running the newest")
	}
	if NewestKernelDiffers("6.1.0-1", boot) {
		t.Fatal("the running kernel isn't in /boot: can't tell")
	}
}
