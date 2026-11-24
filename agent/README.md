# labdex-agent (Go)

The labdex host agent in Go: one static binary that keeps one outgoing WebSocket link to labdex
(mTLS) and runs signed jobs from a fixed list. It does what the Python agent in
`backend/labdex/agent/` does; the contract between them and the hub is
[docs/develop/agent-protocol.md](../docs/develop/agent-protocol.md).

```sh
go test -race ./...                                   # unit and conformance tests
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o labdex-agent ./cmd/labdex-agent
cd ../backend && pytest -q tests/test_go_agent.py     # the binary against a real local labdex
cd ../backend && python tools/agent_rehearsal.py --agent go   # install rehearsal in a systemd container
```

| Package | What |
| --- | --- |
| `cmd/labdex-agent` | The command line: `enrol`, `run`, `health`, `sudoers`, `show` |
| `internal/protocol` | Constants, close codes, the job allowlist, job signature checks |
| `internal/agent` | Config, enrolment, client certificate, the link, job checks, audit, replay guard |
| `internal/jobs` | The job handlers, Ansible bundles |
| `internal/runner` | Running commands, the privileged command lines, `sudoers` |
| `internal/discovery` | Listening ports, processes, systemd units, containers |
| `internal/patch` | Waiting updates, reboot needed |
| `internal/host` | Facts and heartbeat metrics |
| `internal/spool` | The offline buffer |

`testdata/conformance.json` holds cases both agents must pass. It is generated from the Python
agent: `cd backend && python tools/agent_conformance.py`.

One dependency: [`github.com/coder/websocket`](https://github.com/coder/websocket) (ISC), because
Go's standard library has no WebSocket client.
