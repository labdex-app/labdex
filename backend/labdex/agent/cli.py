"""labdex-agent: the command line.

    labdex-agent enrol --url https://lantern.example.net --token <one-time token>
    labdex-agent run                 # what the systemd service runs
    labdex-agent health              # exit 0 while the hub answered in the last two minutes
    labdex-agent sudoers [--user U]  # the sudo rules for this host's capabilities
    labdex-agent show facts|discovery|patches   # run a read-only job here and print it, nothing sent
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys

from . import core


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="labdex-agent", description="The labdex agent: one outgoing link to the hub, signed jobs only.")
    ap.add_argument("--config", default=core.CONFIG)
    ap.add_argument("--version", action="version", version=core.__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enrol", help="swap a one-time token for this host's key")
    e.add_argument("--url")
    e.add_argument("--token", help="or LABDEX_AGENT_TOKEN, or - to read it from stdin")
    e.add_argument("--allow-http", action="store_true", help=argparse.SUPPRESS)  # tests against a local hub
    sub.add_parser("run", help="keep the link to the hub and run its jobs")
    sub.add_parser("health", help="exit 0 while the hub answered in the last two minutes")
    s = sub.add_parser("sudoers", help="print the sudo rules for this host's capabilities")
    s.add_argument("--user", default="labdex-agent")
    sh = sub.add_parser("show", help="run a read-only job here and print the result (nothing is sent)")
    sh.add_argument("what", choices=["facts", "discovery", "patches", "metrics"])
    args = ap.parse_args(argv)

    cfg = core.Config(args.config, url=getattr(args, "url", None))
    if args.cmd == "enrol":
        token = args.token or os.environ.get("LABDEX_AGENT_TOKEN", "")
        if token == "-":
            token = sys.stdin.readline().strip()
        if not token:
            ap.error("give --token (or LABDEX_AGENT_TOKEN)")
        creds = core.enrol(cfg, token, allow_http=args.allow_http)
        print(f"enrolled as {creds['host']} (agent {creds['agent_id']}); start it: systemctl enable --now labdex-agent")
        return 0
    if args.cmd == "health":
        ok, why = core.health(cfg.state_dir)
        print(why)
        return 0 if ok else 1
    if args.cmd == "sudoers":
        from .jobs import sudoers
        sys.stdout.write(sudoers(args.user, cfg.allow))
        return 0
    if args.cmd == "show":
        from . import discovery, host, patch_report
        from .jobs import Runner
        out = {"facts": host.facts, "discovery": lambda: discovery.collect(Runner()),
               "patches": lambda: patch_report.collect(refresh=False), "metrics": lambda: host.Metrics().collect()}[args.what]()
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 0
    # run
    creds = core.load_credentials(cfg.state_dir)
    if creds is None:
        print(f"labdex-agent: not enrolled (no {cfg.state_dir}/credentials.json): run labdex-agent enrol", file=sys.stderr)
        return 2
    agent = core.Agent(cfg, creds, log=lambda m: print(m, flush=True))
    signal.signal(signal.SIGTERM, lambda *_: agent.stop())
    try:
        agent.run()
    except core.Refused as e:
        print(f"labdex-agent: {e}", file=sys.stderr)
        return 3  # RestartPreventExitStatus=3 in the unit: a revoked agent stays stopped
    except KeyboardInterrupt:
        agent.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
