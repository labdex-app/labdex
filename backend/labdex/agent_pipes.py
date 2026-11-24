"""The hub's end of Ansible pipes: SSH connections that reach a host's sshd through its agent's link.

ansible-playbook runs in labdex. For a host whose (Go) agent may take part, ``ssh`` gets
``ProxyCommand=python3 -I <run>/pipe.py <run>/pipe.sock <token> <host>`` (agent_pipe_client.py).
That helper connects to this run's Unix socket; the broker opens a pipe in the host's agent
(``pipe.open``) and moves the bytes both ways as ``pipe.data``. The agent connects the pipe to
``127.0.0.1:<ssh_port>`` on its own host and nowhere else.

A pipe needs an open session on the agent: a signed ``ansible.session`` job, queued the first time
the run reaches that host, so hosts the run never touches get none. The run's end sends
``session.end``. The protocol is docs/develop/agent-protocol.md ("Ansible over the link").

Everything here lives in the labdex process that holds the agents' links: the link threads call
``linked``/``unlinked``/``from_agent``; the Ansible runner makes a ``Run``.
"""
from __future__ import annotations

import hmac
import os
import queue
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path

from .agent import protocol

PIPE_CHUNK = 32 * 1024  # the agent's limit for one pipe.data, before base64
OPEN_WAIT = 15  # seconds for the agent to reach sshd
SESSION_WAIT = 30  # seconds for the agent to take the ansible.session job
SESSION_MINUTES = 60

_lock = threading.Lock()
_links: dict[str, tuple] = {}  # agent id -> (send, gone event)
_pipes: dict[str, Pipe] = {}


class PipeError(Exception):
    pass


# ----------------------------------------------------------------------------- the links
def linked(agent_id: str, send, gone: threading.Event) -> None:
    with _lock:
        _links[agent_id] = (send, gone)


def unlinked(agent_id: str, send) -> None:
    """The link ended: its pipes are gone (the agent closes its end on its own)."""
    with _lock:
        if _links.get(agent_id, (None,))[0] is send:
            _links.pop(agent_id, None)
        mine = [p for p in _pipes.values() if p.agent_id == agent_id]
    for p in mine:
        p.close("the agent's link closed", tell=False)


def is_linked(agent_id: str) -> bool:
    with _lock:
        link = _links.get(agent_id)
    return link is not None and not link[1].is_set()


def send_to(agent_id: str, message: dict) -> bool:
    with _lock:
        link = _links.get(agent_id)
    if link is None or link[1].is_set():
        return False
    try:
        link[0](message)
        return True
    except Exception:  # a link that just dropped
        return False


def from_agent(agent_id: str, msg: dict) -> None:
    """pipe.opened, pipe.data, pipe.close from an agent's link (its reader thread)."""
    with _lock:
        p = _pipes.get(str(msg.get("id", "")))
    if p is None or p.agent_id != agent_id:
        return  # not ours, or another agent's id: ignored
    t = msg.get("t")
    if t == "pipe.opened":
        p.opened.set()
    elif t == "pipe.data":
        try:
            data = protocol.unb64(str(msg.get("data", "")))
        except ValueError:
            p.close("bad pipe.data from the agent", tell=True)
            return
        p.down += len(data)
        p.out.put(data)
    elif t == "pipe.close":
        p.close(str(msg.get("error") or ""), tell=False)


# ----------------------------------------------------------------------------- one pipe
class Pipe:
    def __init__(self, agent_id: str, conn: socket.socket):
        self.id = secrets.token_hex(8)
        self.agent_id = agent_id
        self.conn = conn
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.error = ""
        self.out: queue.Queue = queue.Queue()
        self.up = self.down = 0
        self._once = threading.Lock()

    def close(self, error: str = "", tell: bool = True) -> None:
        with self._once:
            if self.closed.is_set():
                return
            self.closed.set()
        self.error = error
        with _lock:
            _pipes.pop(self.id, None)
        if tell:
            send_to(self.agent_id, {"t": "pipe.close", "id": self.id})
        self.out.put(None)
        self.opened.set()  # wakes a waiting open

    def pump_up(self) -> None:
        """ssh → agent."""
        try:
            while not self.closed.is_set():
                data = self.conn.recv(PIPE_CHUNK)
                if not data:
                    break
                self.up += len(data)
                if not send_to(self.agent_id, {"t": "pipe.data", "id": self.id, "data": protocol.b64(data)}):
                    self.close("the agent's link is down", tell=False)
                    return
        except OSError:
            pass
        self.close("", tell=True)

    def pump_down(self) -> None:
        """agent → ssh; a queue of its own so a slow ssh never holds up the link."""
        try:
            while True:
                data = self.out.get()
                if data is None:
                    break
                self.conn.sendall(data)
        except OSError:
            self.close("ssh went away", tell=True)
        finally:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.conn.close()


# ----------------------------------------------------------------------------- one Ansible run
class Run:
    """The pipes of one ansible-playbook run: a Unix socket in the run's private folder, a token,
    the hosts that go through their agent, and the sessions opened so far."""

    def __init__(self, app, run_dir: Path, hosts: dict[str, str], by: str = "ansible"):
        self.app = app
        self.hosts = dict(hosts)  # host name -> agent id
        self.by = by
        self.token = secrets.token_urlsafe(24)
        self.session = "run-" + uuid.uuid4().hex[:24]
        self.sessions: set[str] = set()  # agent ids with an open session
        self.failed: dict[str, str] = {}
        self._session_lock = threading.Lock()
        self.path = Path(run_dir) / "pipe.sock"
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            self._sock.bind(str(self.path))
        finally:
            os.umask(old)
        self._sock.listen(16)
        self._stop = threading.Event()
        self._mine: list[Pipe] = []
        threading.Thread(target=self._accept, name="agent-pipes", daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        for p in list(self._mine):
            p.close("the run is over", tell=True)
        for agent_id in self.sessions:
            send_to(agent_id, {"t": "session.end", "session": self.session})
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- accepting the helper's connections
    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), name="agent-pipe", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        def refuse(why: str) -> None:
            try:
                conn.sendall(f"error {why}\n".encode())
            except OSError:
                pass
            conn.close()

        try:
            conn.settimeout(10)
            head = b""
            while not head.endswith(b"\n") and len(head) < 512:
                b = conn.recv(1)
                if not b:
                    break
                head += b
            conn.settimeout(None)
        except OSError:
            conn.close()
            return
        parts = head.decode("utf-8", "replace").split()
        if len(parts) != 2 or not hmac.compare_digest(parts[0], self.token):
            refuse("not this run's token")
            return
        host = parts[1]
        agent_id = self.hosts.get(host)
        if agent_id is None:
            refuse(f"{host} doesn't go through its agent in this run")
            return
        try:
            self._open_session(host, agent_id)
        except PipeError as e:
            refuse(str(e))
            return
        p = Pipe(agent_id, conn)
        with _lock:
            _pipes[p.id] = p
        self._mine.append(p)
        if not send_to(agent_id, {"t": "pipe.open", "id": p.id, "session": self.session}):
            p.close("the agent's link is down", tell=False)
            refuse(f"the agent on {host} isn't linked")
            return
        p.opened.wait(OPEN_WAIT)
        if p.closed.is_set() or not p.opened.is_set():
            why = p.error or f"the agent on {host} didn't open the pipe in {OPEN_WAIT} s"
            p.close(why, tell=True)
            refuse(why)
            return
        try:
            conn.sendall(b"ok\n")
        except OSError:
            p.close("ssh went away", tell=True)
            return
        threading.Thread(target=p.pump_down, name="agent-pipe-down", daemon=True).start()
        p.pump_up()

    def _open_session(self, host: str, agent_id: str) -> None:
        """The first pipe to a host opens its session (a signed job); later ones reuse it."""
        with self._session_lock:
            if agent_id in self.sessions:
                return
            if host in self.failed:
                raise PipeError(self.failed[host])
            try:
                self._queue_session(agent_id)
            except PipeError as e:
                self.failed[host] = str(e)
                raise
            self.sessions.add(agent_id)

    def _queue_session(self, agent_id: str) -> None:
        from . import agents
        from .models import Agent, AgentJob, db
        with self.app.app_context():
            try:
                a = db.session.get(Agent, agent_id)
                if a is None or a.revoked_at is not None:
                    raise PipeError("the agent is gone or revoked")
                try:
                    j = agents.queue(a, "ansible.session", {"session": self.session, "minutes": SESSION_MINUTES},
                                     by=self.by, trigger="ansible")
                except agents.AgentError as e:
                    raise PipeError(str(e)) from None
                db.session.commit()
                job_id = j.id
                deadline = time.monotonic() + SESSION_WAIT
                while time.monotonic() < deadline:
                    db.session.expire_all()
                    j = db.session.get(AgentJob, job_id)
                    if j.status in ("succeeded", "failed", "refused", "expired", "lost", "cancelled"):
                        break
                    time.sleep(0.2)
                if j.status != "succeeded":
                    raise PipeError(f"the agent didn't open the session ({j.status}{': ' + j.error if j.error else ''})")
            finally:
                db.session.remove()
