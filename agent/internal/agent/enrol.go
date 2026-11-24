package agent

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/labdex-app/labdex/agent/internal/protocol"
)

// NewRequest is a fresh EC P-256 key and a certificate request for it. The hub uses only the
// key: it names the certificate after the agent itself.
func NewRequest() (*ecdsa.PrivateKey, string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, "", err
	}
	der, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
		Subject: pkix.Name{CommonName: "labdex-agent"}, SignatureAlgorithm: x509.ECDSAWithSHA256}, key)
	if err != nil {
		return nil, "", err
	}
	return key, string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der})), nil
}

// SaveCertificate keeps the certificate with its key in one file, written at once, so a reconnect
// never pairs a new certificate with an old key.
func SaveCertificate(stateDir string, key *ecdsa.PrivateKey, certPEM string) error {
	block, _ := pem.Decode([]byte(certPEM))
	if block == nil || block.Type != "CERTIFICATE" {
		return errors.New("not a PEM certificate")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return err
	}
	pub, ok := cert.PublicKey.(*ecdsa.PublicKey)
	if !ok || !pub.Equal(&key.PublicKey) {
		return errors.New("the certificate is for another key")
	}
	pkcs8, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return err
	}
	out := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Raw})
	out = append(out, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: pkcs8})...)
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return err
	}
	return WritePrivate(filepath.Join(stateDir, ClientPEM), out)
}

// CertificateInfo is the SHA-256 and expiry of the saved client certificate; empty when none.
func CertificateInfo(stateDir string) (sha string, notAfter time.Time) {
	b, err := os.ReadFile(filepath.Join(stateDir, ClientPEM))
	if err != nil {
		return "", time.Time{}
	}
	for {
		var block *pem.Block
		block, b = pem.Decode(b)
		if block == nil {
			return "", time.Time{}
		}
		if block.Type == "CERTIFICATE" {
			sum := sha256.Sum256(block.Bytes)
			if c, err := x509.ParseCertificate(block.Bytes); err == nil {
				notAfter = c.NotAfter
			}
			return hex.EncodeToString(sum[:]), notAfter
		}
	}
}

// Credentials is credentials.json.
type Credentials struct {
	AgentID    string `json:"agent_id"`
	Key        string `json:"key"`
	Host       string `json:"host"`
	SigningKey string `json:"signing_key"`
	URL        string `json:"url"`
}

// LoadCredentials reads credentials.json; nil when there are none.
func LoadCredentials(stateDir string) *Credentials {
	b, err := os.ReadFile(filepath.Join(stateDir, "credentials.json"))
	if err != nil {
		return nil
	}
	var c Credentials
	if json.Unmarshal(b, &c) != nil {
		return nil
	}
	return &c
}

// noRedirect: the token (or the key) would go along.
func noRedirect(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }

// HTTPClient is a client for the hub: the agent's TLS settings, no redirects.
func (c *Config) HTTPClient(timeout time.Duration) (*http.Client, error) {
	tlsCfg, err := c.TLS()
	if err != nil {
		return nil, err
	}
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.TLSClientConfig = tlsCfg
	return &http.Client{Transport: tr, Timeout: timeout, CheckRedirect: noRedirect}, nil
}

// Enrol swaps a one-time enrolment token for this host's own key and writes credentials.json.
// root < 0 takes it from the effective uid.
func Enrol(cfg *Config, token string, allowHTTP bool) (map[string]any, error) {
	if cfg.URL == "" {
		return nil, errors.New("labdex-agent: set url in the config or pass --url")
	}
	if !strings.HasPrefix(cfg.URL, "https://") && !allowHTTP {
		return nil, errors.New("labdex-agent: the hub URL must be https:// (the key travels over it)")
	}
	key, csr, err := NewRequest()
	if err != nil {
		return nil, err
	}
	hostname, _ := os.Hostname()
	body, _ := json.Marshal(map[string]any{"token": token, "hostname": hostname, "version": Version, "csr": csr,
		"protocol": protocol.Protocol, "root": os.Geteuid() == 0, "allow": cfg.AllowList()})
	client, err := cfg.HTTPClient(30 * time.Second)
	if err != nil {
		return nil, fmt.Errorf("labdex-agent: %w", err)
	}
	req, _ := http.NewRequest(http.MethodPost, cfg.URL+"/api/agents/enrol", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Requested-With", "labdex-agent")
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("labdex-agent: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, protocol.MaxMessage))
	if resp.StatusCode != http.StatusOK {
		detail := []rune(strings.ToValidUTF8(string(raw), "�"))
		if len(detail) > 300 {
			detail = detail[:300]
		}
		return nil, fmt.Errorf("labdex-agent: enrolment refused (%d): %s", resp.StatusCode, string(detail))
	}
	var creds map[string]any
	if err := json.Unmarshal(raw, &creds); err != nil {
		return nil, errors.New("labdex-agent: the hub's answer isn't JSON")
	}
	for _, k := range []string{"agent_id", "key", "host", "signing_key"} {
		if s, ok := creds[k].(string); !ok || s == "" {
			return nil, fmt.Errorf("labdex-agent: the hub's answer has no %s", k)
		}
	}
	creds["url"] = cfg.URL
	cert, _ := creds["client_cert"].(string)
	delete(creds, "client_cert")
	delete(creds, "ca_cert")
	if err := os.MkdirAll(cfg.StateDir, 0o700); err != nil {
		return nil, err
	}
	_ = os.Remove(filepath.Join(cfg.StateDir, ClientPEM)) // an older enrolment's: worthless now
	if cert != "" {
		if err := SaveCertificate(cfg.StateDir, key, cert); err != nil {
			return nil, fmt.Errorf("labdex-agent: the client certificate: %w", err)
		}
	}
	out, _ := json.MarshalIndent(creds, "", "  ")
	if err := WritePrivate(filepath.Join(cfg.StateDir, "credentials.json"), out); err != nil {
		return nil, err
	}
	return creds, nil
}
