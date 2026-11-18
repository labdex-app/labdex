"""The agent: enrol once, then keep one outgoing WebSocket link to the hub and run the jobs it sends.

Hosts need no inbound port. The link loop is modelled on floorbit-agent's (bitsandbricks-ch/floorbit-agent,
floorbit_agent/__init__.py, AGPL-3.0-only, the same author and licence): a connect timeout, backoff up
to MAX_BACKOFF, an idle timeout, no redirects (the key would go along), stopping for good when the
key is refused, and a heartbeat file for `labdex-agent health`.

Files (all in the state folder, /var/lib/labdex-agent, owned by the agent's user, mode 0600):

    credentials.json   agent id, key, host name, hub URL and the hub's job-signing public key
    seen.json          ids of jobs already run, until they expire (no replays)
    audit.log          one JSON line per job received, refused, started and finished
    buffer/            results and heartbeats waiting for the hub (spool.py)
    heartbeat          the time the hub last answered
"""
from __future__ import annotations

import configparser
import contextlib
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from . import host, protocol
from .jobs import HANDLERS, Context, Runner
from .protocol import BadJob

__version__ = "0.1.0"

CONFIG = "/etc/labdex-agent/agent.conf"
STATE = "/var/lib/labdex-agent"
CONNECT_TIMEOUT = 15
LINK_IDLE = 90            # the hub answers every heartbeat: quiet this long, it's gone
MAX_BACKOFF = 60
HEALTHY_FOR = 120
BUNDLE_WAIT = 120


class Refused(RuntimeError):
    """The hub won't take this agent (unknown or revoked key, root not allowed, protocol): stop, don't retry."""


# ----------------------------------------------------------------------------- config
class Config:
    """/etc/labdex-agent/agent.conf, an INI file:

        [agent]
        url = https://lantern.example.net
        # write capabilities this host accepts (patch, reboot, ansible); empty: read-only
        allow =
        # a CA bundle for a hub with a private certificate authority
        ca_file =
        state_dir = /var/lib/labdex-agent
    """

    def __init__(self, path: str = CONFIG, **override):
        cp = configparser.ConfigParser()
        cp.read(path)
        s = cp["agent"] if cp.has_section("agent") else {}
        self.url = str(override.get("url") or s.get("url", "")).rstrip("/")
        allow = override.get("allow")
        if allow is None:
            allow = [a.strip() for a in s.get("allow", "").replace(",", " ").split() if a.strip()]
        unknown = set(allow) - set(protocol.CAPABILITIES)
        if unknown:
            raise SystemExit(f"labdex-agent: unknown capabilities in allow: {', '.join(sorted(unknown))}")
        self.allow = set(allow)
        self.ca_file = override.get("ca_file") or s.get("ca_file", "")
        self.state_dir = override.get("state_dir") or s.get("state_dir", STATE)

    def ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=self.ca_file or None)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx


def _write_private(path: str, data: str) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_credentials(state_dir: str) -> dict | None:
    try:
        with open(os.path.join(state_dir, "credentials.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # the enrolment token would go along
        return None


def enrol(cfg: Config, token: str, *, allow_http: bool = False, root: bool | None = None) -> dict:
    """Swap a one-time enrolment token for this host's own key. Writes credentials.json."""
    if not cfg.url:
        raise SystemExit("labdex-agent: set url in the config or pass --url")
    if not cfg.url.startswith("https://") and not allow_http:
        raise SystemExit("labdex-agent: the hub URL must be https:// (the key travels over it)")
    body = json.dumps({"token": token, "hostname": socket.gethostname(), "version": __version__,
                       "protocol": protocol.PROTOCOL, "root": os.geteuid() == 0 if root is None else root, "allow": sorted(cfg.allow)}).encode()
    req = urllib.request.Request(f"{cfg.url}/api/agents/enrol", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Requested-With": "labdex-agent"})
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=cfg.ssl_context()))
    try:
        with opener.open(req, timeout=30) as r:
            creds = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise SystemExit(f"labdex-agent: enrolment refused ({e.code}): {detail}") from e
    for k in ("agent_id", "key", "host", "signing_key"):
        if not isinstance(creds.get(k), str) or not creds[k]:
            raise SystemExit(f"labdex-agent: the hub's answer has no {k}")
    creds["url"] = cfg.url
    os.makedirs(cfg.state_dir, mode=0o700, exist_ok=True)
    _write_private(os.path.join(cfg.state_dir, "credentials.json"), json.dumps(creds, indent=2))
    return creds


# ----------------------------------------------------------------------------- audit and replay guard
class AuditLog:
    """The agent's own record of every job: one JSON line each, never the job's output."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        line = json.dumps({"ts": round(time.time(), 3), "event": event, **fields}, separators=(",", ":"), default=str)
        with self._lock:
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass  # a full disk must not stop the agent; the hub keeps its own audit


class SeenJobs:
    """Job ids already taken, kept until they expire: a recorded job sent again is refused."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self._ids = {k: float(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError):
            self._ids = {}

    def add(self, job_id: str, expires: float) -> bool:
        """False when the id was seen already."""
        now = time.time()
        with self._lock:
            self._ids = {k: v for k, v in self._ids.items() if v + protocol.MAX_SKEW > now}
            if job_id in self._ids:
                return False
            self._ids[job_id] = expires
            with contextlib.suppress(OSError):
                _write_private(self.path, json.dumps(self._ids))
            return True


# ----------------------------------------------------------------------------- the agent
class Agent:
    def __init__(self, cfg: Config, creds: dict, runner: Runner | None = None, log=print):
        from .spool import Spool
        self.cfg = cfg
        self.creds = creds
        self.url = creds.get("url") or cfg.url
        self.runner = runner or Runner()
        self.log = log
        os.makedirs(cfg.state_dir, mode=0o700, exist_ok=True)
        self.audit = AuditLog(os.path.join(cfg.state_dir, "audit.log"))
        self.seen = SeenJobs(os.path.join(cfg.state_dir, "seen.json"))
        self.spool = Spool(os.path.join(cfg.state_dir, "buffer"), log=log)
        self.heartbeat_file = os.path.join(cfg.state_dir, "heartbeat")
        self.metrics = host.Metrics()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="labdex-agent-job")
        self._stop = threading.Event()
        self._ws = None
        self._ws_lock = threading.Lock()
        self._bundles: dict[str, Future] = {}
        self.heartbeat_every = protocol.HEARTBEAT
        self.last_contact = 0.0

    # -- sending
    def _send(self, message: dict) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            with self._ws_lock:
                ws.send(json.dumps(message, separators=(",", ":"), default=str))
            return True
        except Exception:  # the link dropped: the caller buffers it
            return False

    def _send_or_buffer(self, message: dict) -> None:
        if not self._send(message):
            self.spool.put([message | {"buffered": True}])

    def _replay(self) -> int:
        sent = 0
        while not self._stop.is_set():
            batch = self.spool.oldest()
            if batch is None:
                break
            name, records = batch
            if not all(self._send(r) for r in records):
                break
            self.spool.done(name)
            sent += len(records)
        return sent

    # -- the link
    def _headers(self) -> dict:
        return {protocol.KEY_HEADER: self.creds["key"], protocol.VERSION_HEADER: __version__,
                protocol.PROTOCOL_HEADER: str(protocol.PROTOCOL)}

    def _hello(self) -> dict:
        return {"t": "hello", "version": __version__, "protocol": protocol.PROTOCOL, "hostname": socket.gethostname(),
                "root": self.runner.root, "allow": sorted(self.cfg.allow), "jobs": sorted(HANDLERS),
                "facts": host.facts(), "buffered": len(self.spool)}

    def link(self) -> None:
        """One link: open it, say hello, then take messages until it closes. Raises Refused to stop for good."""
        import simple_websocket
        ws_url = "ws" + self.url[len("http"):] + "/api/agents/link"
        ctx = self.cfg.ssl_context() if ws_url.startswith("wss://") else None
        try:
            ws = simple_websocket.Client.connect(ws_url, headers=self._headers(), ping_interval=25,
                                                 max_message_size=protocol.MAX_MESSAGE, ssl_context=ctx)
        except simple_websocket.ConnectionError as e:
            if getattr(e, "status_code", None) in (401, 403, 426):
                raise Refused(f"the hub refused this agent ({e.status_code}): enrol it again") from e
            raise
        self._ws = ws
        try:
            self._send(self._hello())
            while not self._stop.is_set():
                try:
                    raw = ws.receive(timeout=LINK_IDLE)
                except simple_websocket.ConnectionClosed:
                    code = ws.close_reason
                    if code in protocol.FATAL_CLOSES:
                        raise Refused(f"the hub closed the link for good ({code}: {ws.close_message or ''})") from None
                    raise ConnectionError(f"the hub closed the link ({code})") from None
                if raw is None:
                    raise TimeoutError(f"the link was quiet for {LINK_IDLE} s")
                self._contact()
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self.handle(message)
        finally:
            self._ws = None
            with contextlib.suppress(Exception):
                ws.close()

    def handle(self, message: dict) -> None:
        t = message.get("t")
        if t == "welcome":
            self.heartbeat_every = max(1, min(int(message.get("heartbeat") or protocol.HEARTBEAT), 300))
            self.log(f"[agent] linked to {self.url} as {message.get('host')}")
            n = self._replay()
            if n:
                self.log(f"[agent] sent {n} buffered record(s)")
        elif t == "job":
            self.take(message)
        elif t == "bundle":
            fut = self._bundles.get(str(message.get("sha256")))
            if fut is not None and not fut.done():
                if message.get("error"):
                    fut.set_exception(BadJob(str(message["error"])))
                else:
                    try:
                        fut.set_result(protocol.unb64(str(message.get("data", ""))))
                    except ValueError as e:
                        fut.set_exception(BadJob(f"bad bundle data: {e}"))

    def _contact(self) -> None:
        now = time.time()
        if now - self.last_contact >= 10:
            with contextlib.suppress(OSError):
                with open(self.heartbeat_file, "w", encoding="utf-8") as f:
                    f.write(f"{now:.0f}\n")
        self.last_contact = now

    # -- jobs
    def take(self, message: dict) -> None:
        try:
            env = protocol.verify(self.creds["signing_key"], message, self.creds["agent_id"])
        except BadJob as e:
            self.audit.write("refused", reason=str(e))
            job_id = None
            with contextlib.suppress(Exception):
                job_id = json.loads(message.get("job") or "{}").get("id")
            if isinstance(job_id, str):
                self._send_or_buffer({"t": "result", "id": job_id, "ok": False, "refused": True, "error": str(e)})
            return
        job_id, job_type, params = env["id"], env["type"], env["params"]
        missing = protocol.capabilities_for(job_type, params) - self.cfg.allow
        if missing:
            return self._refuse(env, f"capability not switched on in this host's agent.conf: {', '.join(sorted(missing))}")
        if not self.seen.add(job_id, env["expires"]):
            return self._refuse(env, "job already run (replay)", answer=False)
        self.audit.write("accepted", id=job_id, type=job_type, params=params, by=env.get("by", ""))
        self._send({"t": "ack", "id": job_id})
        self.pool.submit(self._run, env)

    def _refuse(self, env: dict, why: str, answer: bool = True) -> None:
        self.audit.write("refused", id=env["id"], type=env["type"], reason=why)
        if answer:
            self._send_or_buffer({"t": "result", "id": env["id"], "ok": False, "refused": True, "error": why})

    def _run(self, env: dict) -> None:
        job_id, job_type = env["id"], env["type"]
        started = time.time()
        ctx = Context(self.runner, self.cfg.allow, self.cfg.state_dir, fetch_bundle=self.fetch_bundle)
        try:
            result = HANDLERS[job_type](env["params"], ctx)
            ok, error = bool(result.get("ok", True)) if isinstance(result, dict) else True, ""
            if isinstance(result, dict) and result.get("error"):
                error = str(result["error"])
        except BadJob as e:
            result, ok, error = None, False, str(e)
        except Exception as e:  # a failing job is a result, not a dead agent
            result, ok, error = None, False, f"{type(e).__name__}: {e}"
        finished = time.time()
        self.audit.write("finished", id=job_id, type=job_type, ok=ok, error=error[:300], seconds=round(finished - started, 2))
        self._send_or_buffer({"t": "result", "id": job_id, "ok": ok, "result": result, "error": error,
                              "started": started, "finished": finished})

    def fetch_bundle(self, sha256: str) -> bytes:
        fut: Future = Future()
        self._bundles[sha256] = fut
        try:
            if not self._send({"t": "bundle.get", "sha256": sha256}):
                raise BadJob("the link is down: can't fetch the bundle")
            return fut.result(timeout=BUNDLE_WAIT)
        except FutureTimeout:
            raise BadJob("the hub didn't send the bundle in time") from None
        finally:
            self._bundles.pop(sha256, None)

    # -- heartbeats
    def _beat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_every):
            self._send_or_buffer({"t": "heartbeat", "ts": time.time(), "metrics": self.metrics.collect()})

    # -- running
    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.close()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def run(self) -> None:
        """Link until stopped; reconnect with backoff. Raises Refused when the hub won't take this agent."""
        threading.Thread(target=self._beat_loop, name="labdex-agent-beat", daemon=True).start()
        backoff = 1
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.link()
            except Refused:
                raise
            except Exception as e:  # network trouble: wait and try again
                if self._stop.is_set():
                    break
                if time.monotonic() - started > 60:
                    backoff = 1  # it was up a while: a fresh drop, not a hub that keeps refusing
                self.log(f"[agent] no link to {self.url} ({e}); trying again in {backoff} s")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)


def health(state_dir: str, now: float | None = None) -> tuple[bool, str]:
    now = time.time() if now is None else now
    try:
        with open(os.path.join(state_dir, "heartbeat"), encoding="utf-8") as f:
            age = now - float(f.read().strip())
    except (OSError, ValueError):
        return False, "the hub hasn't answered yet"
    if age > HEALTHY_FOR:
        return False, f"the hub last answered {age:.0f} s ago"
    return True, f"the hub answered {age:.0f} s ago"
