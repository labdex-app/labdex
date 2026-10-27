"""What a plugin's Python code gets to work with.

A plugin is a folder with `plugin.yaml` and, optionally, `plugin.py` defining
`class Plugin(labdex.plugins.base.Plugin)`. Methods are found by name:

    run_<action>(params)            -> dict                  mode: run
    plan_<action>(params)           -> list[Change]          mode: plan
    apply_<action>(changes, params) -> dict                  mode: plan (second step)
    export_<action>(params)         -> ExportFile            mode: export
    endpoint_<id>(args)             -> JSON-able             read endpoints
    test()                          -> str                   "Test connection"

Actions can also be pure Ansible (`ansible: playbooks/x.yml` in the manifest);
then plan = `--check --diff` and apply/run = the real run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Nothing heavy at import time: isolated plugins load this module without labdex's web app.


class PluginError(Exception):
    """Raise this for an expected failure; the message is shown to the user."""


@dataclass
class Change:
    op: str  # create | update | delete | ansible | other
    kind: str  # what is changed: alias, record, file, …
    name: str
    summary: str = ""
    before: object = None
    after: object = None
    data: dict = field(default_factory=dict)  # whatever apply_ needs

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExportFile:
    filename: str
    mimetype: str
    body: str | bytes


class InventoryView:
    """Read-only access to the whole inventory (plugins run with system rights)."""
    # each method imports what it needs: this module must stay light (see sandbox.py)

    def hosts(self, include_offline=False) -> list[dict]:
        from .. import naming, store
        from ..models import Host, db
        q = db.select(Host).order_by(Host.name)
        if not include_offline:
            q = q.where(Host.status != "offline")
        return [store.host_to_dict(h) | {"zone_long": naming.zone_long(h.zone)} for h in db.session.scalars(q)]

    def networks(self) -> list[dict]:
        from .. import store
        from ..models import Network, db
        return [store.network_to_dict(n) for n in db.session.scalars(db.select(Network).order_by(Network.name))]

    def aliases(self) -> list[dict]:
        from ..generators import aliases as galiases
        return [a.as_dict() for a in galiases.all_aliases()]

    def custom_aliases(self) -> list[dict]:
        from .. import store
        from ..models import AliasGroup, db
        return [store.alias_to_dict(a) for a in db.session.scalars(db.select(AliasGroup).order_by(AliasGroup.name))]

    def dns_records(self) -> list[dict]:
        from .. import naming
        from ..generators import dns
        domain = naming.get_setting("domain")
        return [{"name": r.name, "fqdn": r.fqdn(domain), "type": r.type, "value": r.value, "host": r.host, "zone": r.zone or domain,
                 "public": r.public} for r in dns.records()]

    def apply_delegated_prefixes(self, observed: dict[str, str], source: str) -> dict:
        """The one write a plugin may make to networks: what the firewall received on its WAN
        interfaces ({"wan": "2001:db8:ab00::/56"}) moves the delegated prefixes along."""
        from .. import prefixes
        return prefixes.apply_observed(observed, source)

    def dns_zones(self) -> list[str]:
        """Every zone labdex writes records into, the inventory domain first."""
        from ..generators import dns
        return dns.zones()

    def dhcp_reservations(self) -> list[dict]:
        """[{network, vlan, mac, ip, hostname, description}] for interfaces with MAC + IPv4."""
        from ..generators import hostfiles
        return hostfiles.dhcp_reservations()

    def dns_zone(self) -> str:
        from ..generators import dns
        return dns.bind()

    def ansible_inventory(self) -> dict:
        from ..generators import hostfiles
        return hostfiles.ansible_inventory()

    def yaml(self) -> str:
        from .. import inventory
        return inventory.export_yaml()

    def settings(self) -> dict:
        from .. import naming
        return naming.settings_dict()

    def features(self) -> dict:
        from .. import features
        return {p: features.to_dict(f) for p, f in features.all_features().items()}


class HttpClient:
    """requests.Session with a base URL, TLS settings and sane timeouts."""

    def __init__(self, base_url: str, verify=True, timeout=15, auth=None, headers=None, log=None):
        import requests
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.verify = verify
        self.s.auth = auth
        self.s.headers.update(headers or {})
        self.timeout = timeout
        self._log = log

    def request(self, method: str, path: str, **kw):
        import requests
        url = path if path.startswith(("http://", "https://")) else f"{self.base}/{path.lstrip('/')}"
        kw.setdefault("timeout", self.timeout)
        try:
            r = self.s.request(method, url, **kw)
        except requests.exceptions.SSLError as exc:
            raise PluginError(f"TLS verification failed for {url}: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise PluginError(f"{method} {url} failed: {exc}") from exc
        if self._log:
            self._log(f"{method} {path} → {r.status_code}")
        if r.status_code in (401, 403):
            raise PluginError(f"{method} {path}: access denied ({r.status_code}); check the credentials and their privileges")
        if r.status_code >= 400:
            raise PluginError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
        return r

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)


@dataclass
class AnsibleResult:
    rc: int
    changed: int
    failed: int
    unreachable: int
    output: str

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.failed and not self.unreachable


RECAP = re.compile(r"ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)")


class Context:
    def __init__(self, name: str, spec, settings: dict, log, job_id: int | None = None, integration=None):
        self.name = name
        self.integration = integration
        self.spec = spec
        self.settings = settings
        self.plugin_dir: Path = spec.path
        self.job_id = job_id
        self._log = log
        self.inventory = InventoryView()
        from .. import discovery
        self.discovery = discovery

    def log(self, message: str) -> None:
        self._log(str(message))

    @property
    def data_dir(self) -> Path:
        """labdex's data directory (for caches a plugin keeps, e.g. a git checkout)."""
        from flask import current_app
        return CallablePath(current_app.config.get("LABDEX_DATA_DIR", "./data"))

    # ----------------------------------------------------------------- state
    @property
    def state(self) -> dict:
        """Plugin-private state kept on the integration (not secrets)."""
        return dict((self.integration.state or {}).get("plugin", {})) if self.integration is not None else {}

    def set_state(self, key: str, value) -> None:
        if self.integration is None:
            return
        st = dict(self.integration.state or {})
        plugin = dict(st.get("plugin", {}))
        plugin[key] = value
        st["plugin"] = plugin
        self.integration.state = st
        from ..models import db
        db.session.flush()

    # ----------------------------------------------------------- host status
    def match_host(self):
        """A function: address, name, FQDN, URL or host:port → inventory host name (or None)."""
        from .. import health
        return health.host_matcher()

    def report_status(self, results: dict) -> dict:
        """What this source knows now: {host: {"state": "up|down|unknown", "detail": "…", "data": {…}}}.
        Hosts left out are no longer reported by this source."""
        from .. import health
        out = health.report(self.integration, results)
        for name, old, new in out["changes"]:
            self.log(f"{name}: {old} → {new}")
        return out

    # --------------------------------------------------------- notifications
    def notify(self, event: str, title: str, body: str = "", link: str = "", data: dict | None = None) -> int:
        """Send a notification to the channels that subscribed to it. host.down and host.up keep
        their names (status integrations); anything else becomes plugin.<plugin id>.<event>."""
        from .. import notify
        name = event if event in ("host.down", "host.up") else f"plugin.{self.spec.id}.{event}"
        return notify.emit(name, title, body, link, data, integration=self.name, commit=False)

    # ------------------------------------------------------------ host binding
    def hosts(self, include_offline=False) -> list[dict]:
        """Hosts this integration applies to (selectors + per-host overrides), each
        with `module_settings`: the host settings with secrets decrypted."""
        from .. import hostmodules
        from . import resolved_fields
        if self.integration is None:
            return [h | {"module_settings": {}} for h in self.inventory.hosts(include_offline)]
        out = []
        for h in hostmodules.enabled_hosts(self.integration, include_offline):
            h["module_settings"] = resolved_fields(self.spec.host_settings, h["module_settings"], hostmodules.vault())
            out.append(h)
        return out

    def host(self, name: str) -> dict | None:
        """One host if this integration applies to it."""
        return next((h for h in self.hosts(include_offline=True) if h["name"] == name), None)

    # ------------------------------------------------------------------ http
    def http(self, base_url: str, *, verify=None, ca_file=None, timeout=15, auth=None, headers=None) -> HttpClient:
        from .. import demo
        if demo.enabled():
            raise PluginError("the demo doesn't connect anywhere")
        if verify is None:
            verify = self.settings.get("verify_tls", True)
        ca_file = ca_file if ca_file is not None else self.settings.get("ca_file") or None
        if verify and ca_file:
            verify = ca_file
        return HttpClient(base_url, verify=verify, timeout=timeout, auth=auth, headers=headers, log=self.log)

    # --------------------------------------------------------------- ansible
    def ansible(self, playbook: str | None = None, *, extravars: dict | None = None, limit: str | None = None,
                check: bool = False, diff: bool = False, inventory_override: dict | None = None,
                base_dir: Path | None = None, roles_path: Path | None = None,
                inline: list | None = None) -> AnsibleResult:
        """Run a playbook from base_dir (default: the plugin folder), or an inline
        playbook (a list of plays) with base_dir as working directory."""
        from .. import demo
        if demo.enabled():
            raise PluginError("the demo doesn't run Ansible")
        if getattr(self.spec, "isolated", False):  # an isolated plugin's playbooks run isolated too
            from . import sandbox
            return sandbox.call(self.spec, self, "__ansible__", (playbook,),
                                {"extravars": extravars, "limit": limit, "check": check, "diff": diff,
                                 "inventory_override": inventory_override, "inline": inline})
        exe = shutil.which("ansible-playbook")
        if exe is None:
            raise PluginError("ansible-playbook isn't installed in this labdex instance (build the image with ANSIBLE=1)")
        import yaml
        base = Path(base_dir or self.plugin_dir).resolve()
        if inline is None:
            pb = (base / playbook).resolve()
            if not pb.is_relative_to(base) or not pb.is_file():
                raise PluginError(f"playbook {playbook} not found in {base}")
        s = self.settings
        with tempfile.TemporaryDirectory(prefix="labdex-ansible-") as tmp:
            tmpp = Path(tmp)
            from .. import ansiblevault
            try:
                # {$secret: name} in feature/host vars: the value, for this run only
                seen: set = set()
                inv = ansiblevault.reveal_all(inventory_override or self.inventory.ansible_inventory(), seen)
                extravars = ansiblevault.reveal_all(extravars or {}, seen)
            except ansiblevault.VaultError as exc:
                raise PluginError(str(exc)) from exc
            allvars = inv["all"].setdefault("vars", {})
            if s.get("ssh_user"):
                allvars["ansible_user"] = s["ssh_user"]
            if s.get("connection"):
                allvars["ansible_connection"] = s["connection"]
            if s.get("ssh_private_key"):
                keyfile = tmpp / "id_key"
                fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(s["ssh_private_key"].strip() + "\n")
                allvars["ansible_ssh_private_key_file"] = str(keyfile)
            _private(tmpp / "inventory.yaml", yaml.safe_dump(inv))
            if inline is not None:
                pb = tmpp / "inline.yml"
                pb.write_text(yaml.safe_dump(inline, sort_keys=False))
            varsfile = tmpp / "vars.json"
            fd = os.open(varsfile, os.O_WRONLY | os.O_CREAT, 0o600)
            with os.fdopen(fd, "w") as f:
                secret_keys = {f["key"] for f in getattr(self.spec, "settings", []) or [] if f.get("type") == "secret"}
                json.dump({"labdex_settings": {k: v for k, v in s.items() if k != "ssh_private_key" and k not in secret_keys},
                           **(extravars or {})}, f)
            cmd = [exe, "-i", str(tmpp / "inventory.yaml"), "-e", f"@{varsfile}", str(pb)]
            if s.get("vault_password"):
                # files encrypted with ansible-vault in the playbook folder or repository
                vpf = tmpp / "vault-pass"
                _private(vpf, s["vault_password"])
                cmd[1:1] = ["--vault-id", f"{s['vault_id']}@{vpf}"] if s.get("vault_id") else ["--vault-password-file", str(vpf)]
            if check:
                cmd.append("--check")
            if diff:
                cmd.append("--diff")
            if limit:
                cmd += ["--limit", limit]
            env = {**os.environ, "HOME": tmp, "ANSIBLE_NOCOLOR": "1", "ANSIBLE_RETRY_FILES_ENABLED": "0",
                   "ANSIBLE_HOST_KEY_CHECKING": "True" if s.get("host_key_checking", True) else "False",
                   "ANSIBLE_LOCAL_TEMP": str(tmpp / "atmp"), "ANSIBLE_FORCE_COLOR": "0",
                   "ANSIBLE_CONFIG": str(tmpp / "none.cfg")}
            if roles_path:
                env["ANSIBLE_ROLES_PATH"] = str(roles_path)
            if s.get("connection") == "local":  # "remote" temp files on this machine: in the run's folder too
                env["ANSIBLE_REMOTE_TMP"] = str(tmpp / "rtmp")

            self.log("$ " + " ".join(c if not c.startswith("@") else "@vars.json" for c in cmd[1:]).replace(str(tmpp) + "/", ""))
            secret_keys_all = {f["key"] for f in getattr(self.spec, "settings", []) or [] if f.get("type") == "secret"}
            mask = ansiblevault.masker(seen | {str(s[k]) for k in secret_keys_all if s.get(k)})
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=base)
            lines = []
            for line in proc.stdout:
                line = mask(line.rstrip())
                lines.append(line)
                self.log(line)
            rc = proc.wait()
        out = "\n".join(lines)
        changed = failed = unreachable = 0
        for m in RECAP.finditer(out):
            changed += int(m.group(2))
            unreachable += int(m.group(3))
            failed += int(m.group(4))
        return AnsibleResult(rc, changed, failed, unreachable, out)


class CallablePath(type(Path())):
    """A Path that can also be called: the reference documents `data_dir()`, the code had a property."""

    def __call__(self):
        return self


def _private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


class Plugin:
    """Base class; override the methods your manifest's actions need."""

    def __init__(self, ctx: Context):
        self.ctx = ctx

    @property
    def settings(self) -> dict:
        return self.ctx.settings

    def log(self, msg: str) -> None:
        self.ctx.log(msg)
