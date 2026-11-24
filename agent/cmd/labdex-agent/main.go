// labdex-agent: the labdex host agent, one outgoing link to the hub, signed jobs only.
//
//	labdex-agent enrol --url https://lantern.example.net --token <one-time token>
//	labdex-agent run                 # what the systemd service runs
//	labdex-agent health              # exit 0 while the hub answered in the last two minutes
//	labdex-agent sudoers [--user U]  # the sudo rules for this host's capabilities
//	labdex-agent show facts|discovery|patches|metrics   # run a read-only collector here, nothing sent
//
// The contract with the hub is docs/develop/agent-protocol.md.
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/labdex-app/labdex/agent/internal/agent"
	"github.com/labdex-app/labdex/agent/internal/discovery"
	"github.com/labdex-app/labdex/agent/internal/host"
	"github.com/labdex-app/labdex/agent/internal/patch"
	"github.com/labdex-app/labdex/agent/internal/runner"
)

const usage = `usage: labdex-agent [--config FILE] [--version] {enrol,run,health,sudoers,show} ...

The labdex agent: one outgoing link to the hub, signed jobs only.

  enrol --url URL --token TOKEN   swap a one-time token for this host's key (--token - reads stdin)
  run                             keep the link to the hub and run its jobs
  health                          exit 0 while the hub answered in the last two minutes
  sudoers [--user USER]           print the sudo rules for this host's capabilities
  show facts|discovery|patches|metrics   run a read-only job here and print it (nothing is sent)
`

func main() { os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }

func run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	top := flag.NewFlagSet("labdex-agent", flag.ContinueOnError)
	top.SetOutput(stderr)
	top.Usage = func() { fmt.Fprint(stderr, usage) }
	config := top.String("config", agent.ConfigPath, "the config file")
	version := top.Bool("version", false, "print the version")
	if err := top.Parse(args); err != nil {
		return 2
	}
	if *version {
		fmt.Fprintln(stdout, agent.Version)
		return 0
	}
	rest := top.Args()
	if len(rest) == 0 {
		top.Usage()
		return 2
	}
	cmd, rest := rest[0], rest[1:]
	sub := flag.NewFlagSet(cmd, flag.ContinueOnError)
	sub.SetOutput(stderr)
	url := sub.String("url", "", "the hub's URL")
	token := sub.String("token", "", "the one-time enrolment token, or LABDEX_AGENT_TOKEN, or - to read it from stdin")
	allowHTTP := sub.Bool("allow-http", false, "") // tests against a local hub
	user := sub.String("user", "labdex-agent", "the agent's user")
	if err := sub.Parse(rest); err != nil {
		return 2
	}
	cfg, err := agent.LoadConfig(*config, *url)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	switch cmd {
	case "enrol":
		t := *token
		if t == "" {
			t = os.Getenv("LABDEX_AGENT_TOKEN")
		}
		if t == "-" {
			line, _ := bufio.NewReader(stdin).ReadString('\n')
			t = strings.TrimSpace(line)
		}
		if t == "" {
			fmt.Fprintln(stderr, "labdex-agent enrol: give --token (or LABDEX_AGENT_TOKEN)")
			return 2
		}
		creds, err := agent.Enrol(cfg, t, *allowHTTP)
		if err != nil {
			fmt.Fprintln(stderr, err)
			return 1
		}
		fmt.Fprintf(stdout, "enrolled as %v (agent %v); start it: systemctl enable --now labdex-agent\n", creds["host"], creds["agent_id"])
		return 0
	case "health":
		ok, why := agent.Health(cfg.StateDir, time.Now())
		fmt.Fprintln(stdout, why)
		if ok {
			return 0
		}
		return 1
	case "sudoers":
		fmt.Fprint(stdout, runner.Sudoers(*user, cfg.Allow))
		return 0
	case "show":
		if sub.NArg() != 1 {
			fmt.Fprintln(stderr, "labdex-agent show: facts, discovery, patches or metrics")
			return 2
		}
		var out any
		switch sub.Arg(0) {
		case "facts":
			out = host.Facts()
		case "discovery":
			out = discovery.Collect(runner.New(), host.Proc)
		case "patches":
			out = patch.Collect(runner.New())
		case "metrics":
			out = host.NewMetrics(host.Proc).Collect()
		default:
			fmt.Fprintln(stderr, "labdex-agent show: facts, discovery, patches or metrics")
			return 2
		}
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(out)
		return 0
	case "run":
		creds := agent.LoadCredentials(cfg.StateDir)
		if creds == nil {
			fmt.Fprintf(stderr, "labdex-agent: not enrolled (no %s/credentials.json): run labdex-agent enrol\n", cfg.StateDir)
			return 2
		}
		a, err := agent.New(cfg, creds, runner.New(), func(m string) { fmt.Fprintln(stdout, m) })
		if err != nil {
			fmt.Fprintln(stderr, "labdex-agent:", err)
			return 1
		}
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGTERM, os.Interrupt)
		go func() { <-sig; a.Stop() }()
		var refused *agent.Refused
		if err := a.Run(); errors.As(err, &refused) {
			fmt.Fprintln(stderr, "labdex-agent:", err)
			return 3 // RestartPreventExitStatus=3 in the unit: a revoked agent stays stopped
		}
		return 0
	}
	top.Usage()
	return 2
}
