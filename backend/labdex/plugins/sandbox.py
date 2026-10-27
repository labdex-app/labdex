"""Isolated plugins: community and unsigned plugin code never runs inside labdex.

Each call (an action, a page, a widget, a test) starts a fresh Python process with an empty
environment. Before the plugin is imported, that process locks itself with Landlock to
Python, the plugin's own folder, a data folder of its own and a private temporary folder.
It can't open labdex's database, `secret.key`, other plugins' folders or another process's
memory, and it can't signal labdex. It asks labdex for what the plugin API offers over a
pipe, and gets only its own instance's settings and host settings.

Official and built-in plugins run inside labdex as before: they ship with labdex, are
signed with the project key, and use labdex's own modules.

LABDEX_PLUGIN_SANDBOX: `on` (default) isolates community and unsigned plugins, without
Landlock if the kernel lacks it (the Plugins page says so); `strict` refuses to run them
without Landlock; `off` runs them inside labdex like official ones.
"""
from __future__ import annotations

import ast
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

from .base import PluginError

ISOLATED_TRUST = ("community", "unsigned")
LONG = 3600  # actions, exports, Ansible
SHORT = 60  # pages, widgets, endpoints, test
SYSTEM_READ = ["/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin", "/etc"]
DEVICES = ["/dev/null", "/dev/zero", "/dev/random", "/dev/urandom"]
ENV_PASS = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy", "TZ")
class IsolatedError(Exception):
    """An unexpected error inside an isolated plugin; shows as the original type (ZeroDivisionError: …)."""


def _crash(name: str, msg: str) -> Exception:
    import re
    name = re.sub(r"\W", "", name or "") or "Error"
    return type(name, (IsolatedError,), {})(msg)


_abi = None
_hardened = False


def abi() -> int:
    global _abi
    if _abi is None:
        from . import landlock
        _abi = landlock.abi()
    return _abi


def status(mode: str) -> dict:
    """For the Plugins page: how isolated plugins run here."""
    v = abi()
    if mode == "off":
        return {"mode": "off", "landlock": v, "level": "none",
                "detail": "community and unsigned plugins run inside labdex (LABDEX_PLUGIN_SANDBOX=off)"}
    if v:
        return {"mode": mode, "landlock": v, "level": "full",
                "detail": "community and unsigned plugins run in their own process, locked out of labdex's data (Landlock)"}
    return {"mode": mode, "landlock": 0, "level": "refused" if mode == "strict" else "partial",
            "detail": "this kernel has no Landlock: community and unsigned plugins "
                      + ("don't run (LABDEX_PLUGIN_SANDBOX=strict)" if mode == "strict"
                         else "run in their own process with an empty environment, but could still read files labdex can")}


def wanted(spec, mode: str) -> bool:
    return mode != "off" and not spec.builtin and not spec.bundled and spec.trust.get("status") in ISOLATED_TRUST


# ------------------------------------------------------------ the stand-in class
def method_names(path: Path) -> set[str]:
    """Public methods of `class Plugin` (and classes it inherits from in the same file), read
    from the source without running it. Raises SyntaxError for a broken file."""
    tree = ast.parse(path.read_text(), str(path))
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    names: set[str] = set()

    def walk(c, seen):
        if c.name in seen:
            return
        seen.add(c.name)
        for item in c.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and not item.name.startswith("_"):
                names.add(item.name)
        for b in c.bases:
            if isinstance(b, ast.Name) and b.id in classes:
                walk(classes[b.id], seen)
    if "Plugin" not in classes:
        raise SyntaxError("no class Plugin")
    walk(classes["Plugin"], set())
    return names - {"log", "settings"}


def stand_in(spec, path: Path) -> types.ModuleType:
    """A module whose Plugin class runs every method in an isolated process."""
    names = method_names(path)

    def make(n):
        def method(self, *args, **kw):
            return call(spec, self.ctx, n, args, kw)
        method.__name__ = n
        return method
    attrs = {n: make(n) for n in names}
    attrs.update({"__init__": lambda self, ctx: setattr(self, "ctx", ctx), "isolated": True,
                  "settings": property(lambda self: self.ctx.settings), "log": lambda self, m: self.ctx.log(m)})
    mod = types.ModuleType(f"labdex_isolated_{spec.id.replace('-', '_')}")
    mod.Plugin = type("IsolatedPlugin", (), attrs)
    return mod


# ------------------------------------------------------------------- the call
def _harden() -> None:
    """Make labdex's own process non-dumpable: no other process of the same user can read
    its memory or /proc environment, isolated plugins included."""
    global _hardened
    if _hardened or not sys.platform.startswith("linux"):
        return
    import ctypes
    try:
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE
    except (OSError, AttributeError):
        pass
    _hardened = True


def _python_roots() -> list[str]:
    import site
    import sysconfig
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib"),
             sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}
    try:
        roots |= set(site.getsitepackages())
    except AttributeError:
        pass
    roots.add(str(Path(sys.executable).resolve().parent))
    roots.add(str(Path(__file__).resolve().parent.parent))  # the labdex package (code only)
    import certifi
    roots.add(str(Path(certifi.where()).parent))
    return sorted(r for r in roots if r)


def _plan_fs(spec, ctx, tmp: str, data: str) -> tuple[dict, str]:
    from flask import current_app
    protect = [Path(current_app.config["LABDEX_DATA_DIR"]).resolve()]
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if uri.startswith("sqlite:///"):
        protect.append(Path(uri.removeprefix("sqlite:///")).resolve().parent)
    plugin_dir = str(Path(spec.path).resolve())
    read = [*SYSTEM_READ, *_python_roots()]
    for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if os.environ.get(var):
            read.append(os.environ[var])
    unsafe = [r for r in read if any(p == Path(r).resolve() or p.is_relative_to(Path(r).resolve()) for p in protect if Path(r).exists())]
    if unsafe:
        return {"landlock": False}, f"labdex's data folder lies inside {unsafe[0]}, which plugins need to read"
    return {"landlock": True, "read": [*read, plugin_dir], "write": [tmp, data, "/dev/shm"], "files_rw": DEVICES, "tmp": tmp}, ""


def _env(tmp: str) -> dict:
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": tmp, "TMPDIR": tmp, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONUNBUFFERED": "1", "PYTHONHASHSEED": "random"}
    env.update({k: os.environ[k] for k in ENV_PASS if os.environ.get(k)})
    return env


def _handlers(ctx) -> dict:
    from .sandbox_child import DISCOVERY, INVENTORY
    matcher = []

    def match(x):
        if not matcher:
            matcher.append(ctx.match_host())
        return matcher[0](x)
    h = {"state": lambda: ctx.state, "set_state": ctx.set_state, "match": match, "report_status": ctx.report_status,
         "notify": ctx.notify, "hosts": ctx.hosts, "host": ctx.host}
    h.update({f"inventory.{n}": getattr(ctx.inventory, n) for n in INVENTORY})
    h.update({f"discovery.{n}": getattr(ctx.discovery, n) for n in DISCOVERY})
    return h


def call(spec, ctx, method: str, args=(), kw=None, timeout: int | None = None):
    """Run plugin.<method>(*args, **kw) for this context in an isolated process."""
    from flask import current_app

    from .sandbox_child import decode, encode
    mode = current_app.config.get("LABDEX_PLUGIN_SANDBOX", "on")
    if mode == "strict" and not abi():
        raise PluginError("isolated plugins need Landlock, which this kernel doesn't offer (LABDEX_PLUGIN_SANDBOX=strict)")
    _harden()
    if timeout is None:
        timeout = LONG if method.split("_")[0] in ("run", "plan", "apply", "export") or method == "__ansible__" else SHORT
    tmp = tempfile.mkdtemp(prefix="labdex-plugin-")
    data = Path(current_app.config["LABDEX_DATA_DIR"]) / "plugin-data" / ctx.name
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    fs, why = _plan_fs(spec, ctx, tmp, str(data))
    if fs["landlock"] and not abi():
        fs["landlock"], why = False, "this kernel has no Landlock"
    if not fs["landlock"]:
        if mode == "strict":
            shutil.rmtree(tmp, ignore_errors=True)
            raise PluginError(f"can't isolate {spec.id}: {why}")
        fs = {"landlock": False, "tmp": tmp}
    integ = ctx.integration
    info = {"name": ctx.name, "settings": encode(ctx.settings), "job_id": ctx.job_id, "plugin_dir": str(Path(spec.path).resolve()),
            "python": spec.manifest.get("python", "plugin.py"), "data_dir": str(data),
            "spec": {"id": spec.id, "settings": spec.settings, "host_settings": spec.host_settings},
            "integration": {"id": integ.id, "name": integ.name} if integ is not None else None,
            "demo": bool(current_app.config.get("LABDEX_DEMO")), "sandbox": fs,
            "version": current_app.config.get("LABDEX_VERSION", "")}
    to_child_r, to_child_w = os.pipe()
    from_child_r, from_child_w = os.pipe()
    child = str(Path(__file__).resolve().with_name("sandbox_child.py"))
    boot = ("import importlib.util as u; s = u.spec_from_file_location('labdex_sandbox_child', " + repr(child) + "); "
            f"c = u.module_from_spec(s); s.loader.exec_module(c); c.IN_FD, c.OUT_FD = {to_child_r}, {from_child_w}; c.main()")
    proc = subprocess.Popen([sys.executable, "-I", "-c", boot], cwd=tmp, env=_env(tmp), pass_fds=(to_child_r, from_child_w),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    os.close(to_child_r)
    os.close(from_child_w)
    noise: list[str] = []

    def drain():  # anything the process writes outside the protocol (a crash in C, say)
        for line in proc.stdout:
            if len(noise) < 200:
                noise.append(line.decode(errors="replace").rstrip())
    threading.Thread(target=drain, daemon=True).start()
    w = os.fdopen(to_child_w, "w", encoding="utf-8")
    pending = bytearray()  # raw reads: a buffered reader would hide lines from select()

    def readline(left: float) -> str | None:
        """The next line, "" at the end, None if nothing came within `left` seconds."""
        while b"\n" not in pending:
            ready, _, _ = select.select([from_child_r], [], [], left)
            if not ready:
                return None
            chunk = os.read(from_child_r, 65536)
            if not chunk:
                return ""
            pending.extend(chunk)
        i = pending.index(b"\n")
        line = bytes(pending[:i + 1])
        del pending[:i + 1]
        return line.decode("utf-8")
    handlers = _handlers(ctx)
    deadline = time.monotonic() + timeout

    def send(msg):
        w.write(json.dumps(msg, separators=(",", ":")) + "\n")
        w.flush()
    try:
        send({"t": "start", "info": info, "method": method, "args": encode(list(args)), "kw": encode(kw or {})})
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise PluginError(f"{spec.id}.{method} took longer than {timeout} s and was stopped")
            line = readline(min(left, 5))
            if line is None:
                continue
            if not line:
                proc.wait(timeout=5)
                detail = "; ".join(noise[-5:])
                raise PluginError(f"{spec.id}: the isolated process ended unexpectedly (exit {proc.returncode})" + (f": {detail}" if detail else ""))
            msg = json.loads(line)
            t = msg.get("t")
            if t == "log":
                ctx.log(str(msg.get("msg", ""))[:4000])
            elif t == "rpc":
                fn = handlers.get(msg.get("fn"))
                if fn is None:
                    send({"t": "err", "id": msg.get("id"), "error": f"not part of the plugin API: {msg.get('fn')}"})
                    continue
                try:
                    value = fn(*decode(msg.get("args") or []), **decode(msg.get("kw") or {}))
                    send({"t": "ret", "id": msg.get("id"), "value": encode(value)})
                except Exception as exc:
                    send({"t": "err", "id": msg.get("id"), "error": str(exc) or type(exc).__name__})
            elif t == "done":
                return decode(msg.get("value"))
            elif t == "fail":
                if msg.get("trace"):
                    for tl in msg["trace"].rstrip().splitlines()[-12:]:
                        ctx.log(tl)
                err = msg.get("error", "failed")
                if msg.get("plugin_error"):
                    raise PluginError(err)
                raise _crash(msg.get("type", "Error"), err)
    finally:
        for close in (w.close, lambda: os.close(from_child_r)):
            try:
                close()
            except OSError:
                pass
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
