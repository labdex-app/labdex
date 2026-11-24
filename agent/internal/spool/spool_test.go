package spool

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func quiet(string) {}

func TestPutOldestDone(t *testing.T) {
	dir := t.TempDir()
	s, err := Open(dir, quiet)
	if err != nil {
		t.Fatal(err)
	}
	s.Put([]map[string]any{{"t": "result", "id": "a"}})
	s.Put([]map[string]any{{"t": "heartbeat"}, {"t": "heartbeat"}})
	if s.Len() != 3 {
		t.Fatalf("len %d", s.Len())
	}
	name, recs, ok := s.Oldest()
	if !ok || name != "000000000000.jsonl" || len(recs) != 1 || recs[0]["id"] != "a" {
		t.Fatalf("oldest: %s %v", name, recs)
	}
	s.Done(name, false)
	// reopened, it carries on counting after the newest file and drops leftovers
	_ = os.WriteFile(filepath.Join(dir, "000000000005.jsonl.tmp"), []byte("half"), 0o600)
	s2, _ := Open(dir, quiet)
	if s2.Len() != 2 {
		t.Fatalf("reopened len %d", s2.Len())
	}
	s2.Put([]map[string]any{{"t": "x"}})
	if _, err := os.Stat(filepath.Join(dir, "000000000002.jsonl")); err != nil {
		t.Fatal("next file name")
	}
	if _, err := os.Stat(filepath.Join(dir, "000000000005.jsonl.tmp")); err == nil {
		t.Fatal("leftover .tmp kept")
	}
}

func TestDamagedAndLimits(t *testing.T) {
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, "000000000000.jsonl"), []byte("{broken\n"), 0o600)
	s, _ := Open(dir, quiet)
	s.Put([]map[string]any{{"n": 1}})
	name, _, ok := s.Oldest()
	if !ok || name != "000000000001.jsonl" || s.Dropped() != 1 {
		t.Fatalf("damaged file not skipped: %s, dropped %d", name, s.Dropped())
	}
	s.MaxBytes = 10
	s.Put([]map[string]any{{"n": 2, "pad": "xxxxxxxxxxxxxxxxxxxx"}})
	if s.Len() != 0 {
		t.Fatalf("size limit: %d left", s.Len())
	}
	s.MaxBytes = MaxBytes
	s.Put([]map[string]any{{"n": 3}})
	if s.Trim(time.Now().Add(8*24*time.Hour)) != 1 || s.Len() != 0 {
		t.Fatal("age limit")
	}
}
