"""The isolated side of a plugin call. labdex starts this as its own Python process with an
empty environment; it locks itself out of everything but Python, the plugin's own folder,
its data folder and a private temporary folder (Landlock), then imports the plugin and
runs one method. Everything the plugin asks of labdex goes over a pipe to the parent,
which answers only what the plugin API offers, for this instance only.

Messages are JSON lines. Parent → child: {"t": "start", ...}, then replies {"t": "ret"|"err"}.
Child → parent: {"t": "rpc", "id", "fn", "args", "kw"}, {"t": "log", "msg"}, and finally
{"t": "done", "value"} or {"t": "fail", "error", "plugin_error", "trace"}.
"""
from __future__ import annotations

import base64
import dataclasses
import io
import json
import os
import sys
from pathlib import Path, PurePath

IN_FD, OUT_FD = 3, 4


# ------------------------------------------------------------------ wire format
def encode(obj):
    from labdex.plugins.base import AnsibleResult, Change, ExportFile
    if isinstance(obj, Change):
        return {"__change__": {k: encode(v) for k, v in obj.as_dict().items()}}
    if isinstance(obj, ExportFile):
        body = obj.body
        binary = isinstance(body, bytes)
        return {"__export__": {"filename": obj.filename, "mimetype": obj.mimetype, "binary": binary,
                               "body": base64.b64encode(body).decode() if binary else body}}
    if isinstance(obj, AnsibleResult):
        return {"__ansible__": dataclasses.asdict(obj)}
    if isinstance(obj, dict):
        return {str(k): encode(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [encode(v) for v in obj]
    if isinstance(obj, PurePath):
        return str(obj)
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode()
    if obj is None or isinstance(obj, str | int | float | bool):
        return obj
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def decode(obj):
    from labdex.plugins.base import AnsibleResult, Change, ExportFile
    if isinstance(obj, dict):
        if "__change__" in obj:
            return Change(**{k: decode(v) for k, v in obj["__change__"].items()})
        if "__export__" in obj:
            e = obj["__export__"]
            return ExportFile(e["filename"], e["mimetype"], base64.b64decode(e["body"]) if e["binary"] else e["body"])
        if "__ansible__" in obj:
            return AnsibleResult(**obj["__ansible__"])
        return {k: decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(v) for v in obj]
    return obj


class _Wire:
    def __init__(self):
        self.r = os.fdopen(IN_FD, "r", encoding="utf-8")
        self.w = os.fdopen(OUT_FD, "w", encoding="utf-8")
        self.n = 0

    def send(self, msg: dict) -> None:
        self.w.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self.w.flush()

    def recv(self) -> dict:
        line = self.r.readline()
        if not line:
            raise SystemExit(3)  # labdex went away
        return json.loads(line)

    def call(self, fn: str, *args, **kw):
        from labdex.plugins.base import PluginError
        self.n += 1
        self.send({"t": "rpc", "id": self.n, "fn": fn, "args": encode(list(args)), "kw": encode(kw)})
        msg = self.recv()
        if msg.get("t") == "err":
            raise PluginError(msg.get("error", "labdex refused"))
        return decode(msg.get("value"))


class _Out(io.TextIOBase):
    """print() in a plugin ends up in the job log."""

    def __init__(self, wire):
        self.wire, self.buf = wire, ""

    def writable(self):
        return True

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.wire.send({"t": "log", "msg": line})
        return len(s)

    def flush(self):
        if self.buf:
            self.wire.send({"t": "log", "msg": self.buf})
            self.buf = ""


# ---------------------------------------------------------------- the context
class _Remote:
    def __init__(self, wire, prefix, names):
        for n in names:
            setattr(self, n, (lambda fn: lambda *a, **kw: wire.call(fn, *a, **kw))(f"{prefix}.{n}"))


INVENTORY = ("hosts", "networks", "aliases", "custom_aliases", "dns_records", "apply_delegated_prefixes", "dns_zones",
             "dhcp_reservations", "dns_zone", "ansible_inventory", "yaml", "settings", "features")
DISCOVERY = ("record_scan", "record_neighbours", "record_links", "record_macs")


class ChildContext:
    """What a plugin's `self.ctx` is when it runs isolated: the plugin API, nothing more."""

    def __init__(self, wire, info: dict):
        from types import SimpleNamespace
        self._wire = wire
        self.name = info["name"]
        self.settings = decode(info["settings"])
        self.job_id = info.get("job_id")
        self.plugin_dir = Path(info["plugin_dir"])
        self.spec = SimpleNamespace(**info["spec"])
        self.integration = SimpleNamespace(**info["integration"]) if info.get("integration") else None
        from labdex.plugins.base import CallablePath
        self._data_dir = CallablePath(info["data_dir"])
        self._demo = bool(info.get("demo"))
        self.inventory = _Remote(wire, "inventory", INVENTORY)
        self.discovery = _Remote(wire, "discovery", DISCOVERY)

    def log(self, message) -> None:
        self._wire.send({"t": "log", "msg": str(message)})

    @property
    def data_dir(self) -> Path:
        """A folder of this instance's own (for caches); kept between runs."""
        return self._data_dir

    @property
    def state(self) -> dict:
        return self._wire.call("state")

    def set_state(self, key, value) -> None:
        self._wire.call("set_state", key, value)

    def match_host(self):
        cache = {}

        def match(x):
            if x not in cache:
                cache[x] = self._wire.call("match", x)
            return cache[x]
        return match

    def report_status(self, results):
        return self._wire.call("report_status", results)

    def notify(self, event, title, body="", link="", data=None):
        return self._wire.call("notify", event, title, body, link, data)

    def hosts(self, include_offline=False):
        return self._wire.call("hosts", include_offline)

    def host(self, name):
        return self._wire.call("host", name)

    def http(self, base_url, *, verify=None, ca_file=None, timeout=15, auth=None, headers=None):
        from labdex.plugins.base import HttpClient, PluginError
        if self._demo:
            raise PluginError("the demo doesn't connect anywhere")
        if verify is None:
            verify = self.settings.get("verify_tls", True)
        ca_file = ca_file if ca_file is not None else self.settings.get("ca_file") or None
        if verify and ca_file:
            verify = ca_file
        return HttpClient(base_url, verify=verify, timeout=timeout, auth=auth, headers=headers, log=self.log)

    def ansible(self, playbook=None, *, extravars=None, limit=None, check=False, diff=False, inventory_override=None,
                base_dir=None, roles_path=None, inline=None):
        """Runs inside the sandbox too. Global secrets ({$secret: …}) aren't filled in for an
        isolated plugin: they belong to labdex, not to this plugin."""
        from labdex import ansiblevault
        from labdex.plugins.base import Context, PluginError
        if self._demo:
            raise PluginError("the demo doesn't run Ansible")
        dropped = []

        def strip(name):
            dropped.append(name)
            return ""
        inv = ansiblevault.replace(inventory_override or self.inventory.ansible_inventory(), strip)
        extravars = ansiblevault.replace(extravars or {}, strip)
        if dropped:
            self.log(f"isolated plugin: {len(set(dropped))} global secret(s) left empty ({', '.join(sorted(set(dropped)))})")
        return Context.ansible(self, playbook, extravars=extravars, limit=limit, check=check, diff=diff, inventory_override=inv,
                               base_dir=base_dir, roles_path=roles_path, inline=inline)


# ----------------------------------------------------------------------- main
def _lock(info: dict) -> int:
    from labdex.plugins import landlock
    sb = info["sandbox"]
    if not sb.get("landlock"):
        return 0
    return landlock.restrict(read=sb["read"], write=sb["write"], files_rw=sb["files_rw"])


def _light_packages(version: str) -> None:
    """Make `labdex` and `labdex.plugins` importable without running their __init__ (which
    loads the web app: Flask, SQLAlchemy, half a second): a plugin needs the base classes."""
    import types
    root = Path(__file__).resolve().parent.parent
    for name, path in (("labdex", root), ("labdex.plugins", root / "plugins")):
        if name not in sys.modules:
            m = types.ModuleType(name)
            m.__path__ = [str(path)]
            m.__file__ = str(path / "__init__.py")
            sys.modules[name] = m
    sys.modules["labdex"].__version__ = version


def main() -> None:
    wire = _Wire()
    start = wire.recv()
    info = start["info"]
    _light_packages(info.get("version", ""))
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
    except (ImportError, ValueError, OSError):
        pass
    try:
        abi = _lock(info)
    except OSError as exc:
        wire.send({"t": "fail", "error": f"couldn't isolate the plugin: {exc}", "plugin_error": True})
        return
    os.chdir(info["sandbox"]["tmp"])
    sys.stdout = sys.stderr = _Out(wire)
    try:
        import importlib.util
        ctx = ChildContext(wire, info)
        if start["method"] == "__ansible__":  # a manifest-only Ansible action
            value = ctx.ansible(*decode(start["args"]), **decode(start["kw"]))
        else:
            path = Path(info["plugin_dir"]) / info["python"]
            sp = importlib.util.spec_from_file_location(f"labdex_plugin_{info['spec']['id'].replace('-', '_')}", path)
            mod = importlib.util.module_from_spec(sp)
            sys.modules[sp.name] = mod
            sp.loader.exec_module(mod)
            obj = mod.Plugin(ctx)
            value = getattr(obj, start["method"])(*decode(start["args"]), **decode(start["kw"]))
        sys.stdout.flush()
        wire.send({"t": "done", "value": encode(value), "landlock": abi})
    except BaseException as exc:
        import traceback
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            from labdex.plugins.base import PluginError
            is_pe = isinstance(exc, PluginError)
        except Exception:
            is_pe = False
        wire.send({"t": "fail", "error": str(exc) or type(exc).__name__, "plugin_error": is_pe,
                   "type": type(exc).__name__, "trace": "" if is_pe else traceback.format_exc()[-4000:]})


if __name__ == "__main__":
    main()
