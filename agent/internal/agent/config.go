// Package agent is the agent itself: config, enrolment, the client certificate, the link to the
// hub, the job checks, the audit log and the replay guard. The contract is
// docs/develop/agent-protocol.md; the Python agent (backend/labdex/agent) does the same.
package agent

import (
	"bufio"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/labdex-app/labdex/agent/internal/protocol"
)

// Version is the agent's version; the build may set it with -ldflags "-X …/agent.Version=…".
var Version = "0.1.0"

const (
	ConfigPath = "/etc/labdex-agent/agent.conf"
	StateDir   = "/var/lib/labdex-agent"
	ClientPEM  = "client.pem"
)

// Config is /etc/labdex-agent/agent.conf, an INI file with an [agent] section.
type Config struct {
	URL      string
	Allow    map[string]bool
	CAFile   string
	StateDir string
}

// readINI reads the keys of one section the way Python's configparser does for simple files:
// `key = value` or `key: value`, keys case-insensitive, `#` and `;` comment lines.
func readINI(path, section string) map[string]string {
	out := map[string]string{}
	f, err := os.Open(path)
	if err != nil {
		return out
	}
	defer f.Close()
	in := false
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			in = strings.TrimSpace(line[1:len(line)-1]) == section
			continue
		}
		if !in {
			continue
		}
		i := strings.IndexAny(line, "=:")
		if i < 0 {
			continue
		}
		out[strings.ToLower(strings.TrimSpace(line[:i]))] = strings.TrimSpace(line[i+1:])
	}
	return out
}

// LoadConfig reads the config; url (from --url) overrides the file's when set.
func LoadConfig(path, url string) (*Config, error) {
	s := readINI(path, "agent")
	if url == "" {
		url = s["url"]
	}
	c := &Config{URL: strings.TrimRight(url, "/"), Allow: map[string]bool{}, CAFile: s["ca_file"], StateDir: s["state_dir"]}
	if c.StateDir == "" {
		c.StateDir = StateDir
	}
	var unknown []string
	for _, a := range strings.Fields(strings.ReplaceAll(s["allow"], ",", " ")) {
		if !protocol.IsCapability(a) {
			unknown = append(unknown, a)
			continue
		}
		c.Allow[a] = true
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		return nil, fmt.Errorf("labdex-agent: unknown capabilities in allow: %s", strings.Join(unknown, ", "))
	}
	return c, nil
}

// AllowList is the sorted capability list.
func (c *Config) AllowList() []string {
	out := []string{}
	for k := range c.Allow {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// TLS is the client TLS config: TLS 1.2 or newer; the system roots, or only ca_file when it is
// set (as Python's ssl.create_default_context(cafile=…) does); the client certificate when there
// is one, presented when the hub or its proxy asks.
func (c *Config) TLS() (*tls.Config, error) {
	cfg := &tls.Config{MinVersion: tls.VersionTLS12}
	if c.CAFile != "" {
		pem, err := os.ReadFile(c.CAFile)
		if err != nil {
			return nil, err
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(pem) {
			return nil, fmt.Errorf("no certificates in %s", c.CAFile)
		}
		cfg.RootCAs = pool
	}
	if pem, err := os.ReadFile(filepath.Join(c.StateDir, ClientPEM)); err == nil {
		cert, err := tls.X509KeyPair(pem, pem)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", ClientPEM, err)
		}
		cfg.Certificates = []tls.Certificate{cert}
	}
	return cfg, nil
}

// WritePrivate writes a file at once with mode 0600: aside, synced, renamed.
func WritePrivate(path string, data []byte) error {
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}
