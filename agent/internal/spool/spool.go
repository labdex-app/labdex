// Package spool is a disk buffer for what the agent has to say while the hub can't take it: job
// results and heartbeats. The same layout and limits as the Python agent's spool (see "The spool"
// in the agent protocol), so either agent can replay the other's buffer after a switch.
package spool

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	MaxBytes = 64 * 1024 * 1024
	MaxAge   = 7 * 24 * time.Hour
)

var nameRe = regexp.MustCompile(`^(\d{12})\.jsonl$`)

// Spool is one buffer folder.
type Spool struct {
	Folder   string
	MaxBytes int64
	MaxAge   time.Duration
	Log      func(string)

	mu       sync.Mutex
	next     int64
	bytes    int64
	records  int
	dropped  int
	failing  bool
	trimSaid time.Time
}

// Open opens (and makes) the folder; half-written files from a crash are removed.
func Open(folder string, log func(string)) (*Spool, error) {
	if err := os.MkdirAll(folder, 0o700); err != nil {
		return nil, err
	}
	s := &Spool{Folder: folder, MaxBytes: MaxBytes, MaxAge: MaxAge, Log: log}
	files := s.files()
	if len(files) > 0 {
		n, _ := strconv.ParseInt(nameRe.FindStringSubmatch(files[len(files)-1])[1], 10, 64)
		s.next = n + 1
	}
	for _, f := range files {
		s.bytes += s.size(f)
		s.records += s.count(f)
	}
	entries, _ := os.ReadDir(folder)
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".tmp") {
			_ = os.Remove(filepath.Join(folder, e.Name()))
		}
	}
	return s, nil
}

func (s *Spool) files() []string {
	entries, _ := os.ReadDir(s.Folder)
	var out []string
	for _, e := range entries {
		if nameRe.MatchString(e.Name()) {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out
}

func (s *Spool) size(name string) int64 {
	st, err := os.Stat(filepath.Join(s.Folder, name))
	if err != nil {
		return 0
	}
	return st.Size()
}

func (s *Spool) count(name string) int {
	b, err := os.ReadFile(filepath.Join(s.Folder, name))
	if err != nil {
		return 0
	}
	return bytes.Count(b, []byte("\n"))
}

// Len is the number of records waiting.
func (s *Spool) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.records
}

// Dropped is the number of records thrown away: too old, too much, or the disk was full.
func (s *Spool) Dropped() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.dropped
}

// Put writes one batch as one file. False when the disk took nothing.
func (s *Spool) Put(records []map[string]any) bool {
	if len(records) == 0 {
		return true
	}
	var buf bytes.Buffer
	for _, r := range records {
		b, err := json.Marshal(r)
		if err != nil {
			continue
		}
		buf.Write(b)
		buf.WriteByte('\n')
	}
	s.mu.Lock()
	name := fmt.Sprintf("%012d.jsonl", s.next)
	s.next++
	path := filepath.Join(s.Folder, name)
	err := writeSynced(path+".tmp", buf.Bytes())
	if err == nil {
		err = os.Rename(path+".tmp", path)
	}
	if err != nil {
		_ = os.Remove(path + ".tmp")
		s.dropped += len(records)
		if !s.failing {
			s.Log(fmt.Sprintf("[agent] cannot write the buffer in %s: %v; dropping what does not fit", s.Folder, err))
		}
		s.failing = true
		s.mu.Unlock()
		return false
	}
	if s.failing {
		s.Log(fmt.Sprintf("[agent] the buffer in %s can be written again", s.Folder))
	}
	s.failing = false
	s.bytes += int64(buf.Len())
	s.records += len(records)
	s.mu.Unlock()
	s.Trim(time.Now())
	return true
}

func writeSynced(path string, data []byte) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	return err
}

// Oldest is the oldest batch; ok is false when the buffer is empty. A damaged file is dropped,
// so it can't block everything behind it.
func (s *Spool) Oldest() (string, []map[string]any, bool) {
	s.mu.Lock()
	files := s.files()
	s.mu.Unlock()
	for _, name := range files {
		recs, err := read(filepath.Join(s.Folder, name))
		if err != nil {
			s.Log(fmt.Sprintf("[agent] dropping a damaged buffer file %s: %v", name, err))
			s.Done(name, true)
			continue
		}
		return name, recs, true
	}
	return "", nil, false
}

func read(path string) ([]map[string]any, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []map[string]any
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 32*1024*1024)
	for sc.Scan() {
		line := bytes.TrimSpace(sc.Bytes())
		if len(line) == 0 {
			continue
		}
		var r map[string]any
		if err := json.Unmarshal(line, &r); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, sc.Err()
}

// Done removes a batch that went out (or can't).
func (s *Spool) Done(name string, dropped bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	size, count := s.size(name), s.count(name)
	if err := os.Remove(filepath.Join(s.Folder, name)); err != nil {
		return
	}
	s.bytes -= size
	s.records -= count
	if dropped {
		s.dropped += count
	}
}

// Trim drops the oldest batches past the size or age limit; it returns how many records went.
func (s *Spool) Trim(now time.Time) int {
	gone := 0
	for _, name := range s.files() {
		st, err := os.Stat(filepath.Join(s.Folder, name))
		if err != nil {
			continue
		}
		s.mu.Lock()
		over := s.bytes > s.MaxBytes
		s.mu.Unlock()
		if now.Sub(st.ModTime()) <= s.MaxAge && !over {
			break
		}
		before := s.Dropped()
		s.Done(name, true)
		gone += s.Dropped() - before
	}
	if gone > 0 && now.Sub(s.trimSaid) > time.Minute {
		s.Log(fmt.Sprintf("[agent] the buffer in %s is past its limits: dropping the oldest records (%d so far)", s.Folder, s.Dropped()))
		s.trimSaid = now
	}
	return gone
}
