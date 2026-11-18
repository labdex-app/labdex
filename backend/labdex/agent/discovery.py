"""Service discovery on the host, read-only: what listens, which process runs it, which systemd unit
and which container it belongs to.

- Listening sockets come from `ss -Htulpn` through sudo (the only way to see other users'
  processes without root); without the sudo rule, from `ss -Htuln`, with no processes.
- Processes come from /proc: PID, parent, user, command line (with passwords and tokens masked),
  start time and the systemd unit from its cgroup.
- Running units from `systemctl list-units` and `systemctl show`, which need no privileges.
- Containers from `docker ps` and `podman ps` through sudo (being in the docker group is as good as
  root, so the agent's user is never put there).

Nothing here changes the host.
"""
from __future__ import annotations

import json
import os
import re
import time

PROC = "/proc"
MAX_PROCESSES = 500
MAX_CMDLINE = 1000

# a flag or variable whose value is a secret: --password x, --token=x, DB_PASS=x, apikey: x
_SECRET_WORD = r"[\w.-]*(?:pass(?:wd|word|phrase)?|secret|token|api[-_]?key|access[-_]?key|private[-_]?key|credentials?|auth)[\w.-]*"
_SECRET_FLAG = re.compile(rf"^(--?{_SECRET_WORD})(=.*)?$", re.I)
_SECRET_ASSIGN = re.compile(rf"^({_SECRET_WORD})([=:])(.+)$", re.I)
_URL_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]*:)[^@/\s]+@", re.I)
MASK = "***"


def redact(argv: list[str]) -> list[str]:
    """A command line with secrets masked: the value after a secret flag, in a secret assignment, in a URL."""
    out, hide_next = [], False
    for a in argv:
        if hide_next:
            out.append(MASK)
            hide_next = False
            continue
        m = _SECRET_FLAG.match(a)
        if m:
            if m.group(2) is None:
                out.append(a)
                hide_next = True
            else:
                out.append(f"{m.group(1)}={MASK}")
            continue
        m = _SECRET_ASSIGN.match(a)
        if m:
            out.append(f"{m.group(1)}{m.group(2)}{MASK}")
            continue
        out.append(_URL_USERINFO.sub(rf"\1{MASK}@", a))
    return out


# ----------------------------------------------------------------------------- listeners
_SS_USERS = re.compile(r'\("([^"]*)",pid=(\d+),fd=\d+\)')


def _split_addr(text: str) -> tuple[str, int] | None:
    addr, _, port = text.rpartition(":")
    if not port.isdigit():
        return None
    addr = addr.strip("[]").split("%", 1)[0]
    if addr in ("*", ""):
        addr = "0.0.0.0"
    return addr, int(port)


def parse_ss(text: str) -> list[dict]:
    """`ss -Htulpn` (or -Htuln) → listeners: {proto, address, port, pids: [{pid, name}]}."""
    out, seen = [], set()
    for line in text.splitlines():
        f = line.split()
        if len(f) < 5 or f[0] not in ("tcp", "udp") or f[1] not in ("LISTEN", "UNCONN"):
            continue
        hp = _split_addr(f[4])
        if hp is None:
            continue
        procs = [{"name": n, "pid": int(p)} for n, p in _SS_USERS.findall(" ".join(f[6:]))]
        key = (f[0], hp[0], hp[1])
        if key in seen:  # ss lists a socket once per process sharing it; the users column already has them all
            continue
        seen.add(key)
        uniq = {p["pid"]: p for p in procs}
        out.append({"proto": f[0], "address": hp[0], "port": hp[1], "pids": sorted(uniq.values(), key=lambda p: p["pid"])})
    return sorted(out, key=lambda x: (x["port"], x["proto"], x["address"]))


def listeners(runner) -> tuple[list[dict], list[str]]:
    notes = []
    code, out, err = runner.privileged("ss")
    if code != 0:
        if "no new privileges" in err:
            notes.append("no processes for listening ports: sudo can't run under NoNewPrivileges (check the service's hardening)")
        elif "sudo" in err or code == 1:
            notes.append("no processes for listening ports: the sudo rule for ss is missing")
        else:
            notes.append(f"ss with processes failed: {err.strip()[:200]}")
        code, out, err = runner.run(["ss", "-Htuln"])
        if code != 0:
            return [], [*notes, f"ss failed: {err.strip()[:200]}"]
    return parse_ss(out), notes


# ----------------------------------------------------------------------------- processes
def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


_UNIT = re.compile(r"/([^/]+\.(?:service|scope))(?:/|$)")


def unit_of_cgroup(text: str) -> str:
    """The innermost systemd service or scope in /proc/<pid>/cgroup, '' for none."""
    for line in text.splitlines():
        path = line.split(":", 2)[-1]
        units = _UNIT.findall(path)
        if units:
            return units[-1]
    return ""


def _users() -> dict[int, str]:
    try:
        import pwd
        return {p.pw_uid: p.pw_name for p in pwd.getpwall()}
    except ImportError:
        return {}


def process(pid: int, users: dict[int, str], proc: str = PROC, boot: float | None = None) -> dict | None:
    status = _read(f"{proc}/{pid}/status")
    if not status:
        return None
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    uid = int((fields.get("Uid") or "-1").split()[0])
    raw = _read(f"{proc}/{pid}/cmdline")
    argv = [a for a in raw.split("\0") if a] if raw else []
    cmd = " ".join(redact(argv))[:MAX_CMDLINE]
    out = {"pid": pid, "ppid": int((fields.get("PPid") or "0").strip() or 0), "name": (fields.get("Name") or "").strip(),
           "user": users.get(uid, str(uid)), "uid": uid, "cmdline": cmd or f"[{(fields.get('Name') or '').strip()}]",
           "unit": unit_of_cgroup(_read(f"{proc}/{pid}/cgroup"))}
    stat = _read(f"{proc}/{pid}/stat")
    if stat and boot is not None:
        try:
            ticks = int(stat.rsplit(")", 1)[1].split()[19])
            out["started"] = round(boot + ticks / os.sysconf("SC_CLK_TCK"))
        except (IndexError, ValueError, OSError):
            pass
    return out


def processes(pids: set[int], proc: str = PROC) -> dict[int, dict]:
    """The given processes and their parents (one level up), by PID."""
    users = _users()
    try:
        boot = time.time() - float(_read(f"{proc}/uptime").split()[0])
    except (IndexError, ValueError):
        boot = None
    out: dict[int, dict] = {}
    for pid in sorted(pids)[:MAX_PROCESSES]:
        p = process(pid, users, proc, boot)
        if p:
            out[pid] = p
    for p in list(out.values()):
        if p["ppid"] > 1 and p["ppid"] not in out and len(out) < MAX_PROCESSES:
            parent = process(p["ppid"], users, proc, boot)
            if parent:
                out[p["ppid"]] = parent
    for p in out.values():
        parent = out.get(p["ppid"])
        p["parent"] = {"pid": p["ppid"], "name": parent["name"] if parent else ("systemd" if p["ppid"] == 1 else "")}
    return out


# ----------------------------------------------------------------------------- systemd
def parse_show(text: str) -> list[dict]:
    """`systemctl show -p Id,Description,MainPID,User,FragmentPath,ActiveEnterTimestamp a b …` → units."""
    out = []
    for block in text.strip().split("\n\n"):
        f = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if not f.get("Id"):
            continue
        pid = int(f.get("MainPID") or 0)
        out.append({"unit": f["Id"], "description": f.get("Description", ""), "pid": pid or None,
                    "user": f.get("User", "") or "root", "path": f.get("FragmentPath", ""),
                    "since": f.get("ActiveEnterTimestamp", "")})
    return out


def units(runner) -> tuple[list[dict], list[str]]:
    if not runner.has("systemctl"):
        return [], ["no systemd here"]
    code, out, err = runner.run(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--plain", "--no-pager"])
    if code != 0:
        return [], [f"systemctl list-units failed: {err.strip()[:200]}"]
    names = [line.split()[0] for line in out.splitlines() if line.strip()]
    if not names:
        return [], []
    code, out, err = runner.run(["systemctl", "show", "--no-pager", "-p", "Id,Description,MainPID,User,FragmentPath,ActiveEnterTimestamp", "--", *names])
    if code != 0:
        return [], [f"systemctl show failed: {err.strip()[:200]}"]
    return parse_show(out), []


# ----------------------------------------------------------------------------- containers
_PORT = re.compile(r"(?:(\[?[0-9a-fA-F.:]*\]?):)?(\d+)(?:-(\d+))?->(\d+)(?:-\d+)?/(tcp|udp)")


def parse_docker_ports(text: str) -> list[dict]:
    """'0.0.0.0:8080->80/tcp, [::]:8080->80/tcp' → [{address, port, container_port, proto}]"""
    out = []
    for m in _PORT.finditer(text or ""):
        addr, start, end, cport, proto = m.groups()
        for i, p in enumerate(range(int(start), int(end or start) + 1)):
            out.append({"address": (addr or "0.0.0.0").strip("[]") or "0.0.0.0", "port": p, "container_port": int(cport) + i, "proto": proto})
    return out


def parse_docker(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        out.append({"engine": "docker", "id": c.get("ID", "")[:12], "name": c.get("Names", ""), "image": c.get("Image", ""),
                    "status": c.get("Status", ""), "compose_project": _label(c.get("Labels", ""), "com.docker.compose.project"),
                    "ports": parse_docker_ports(c.get("Ports", ""))})
    return out


def _label(labels: str, key: str) -> str:
    for kv in (labels or "").split(","):
        k, _, v = kv.partition("=")
        if k == key:
            return v
    return ""


def parse_podman(text: str) -> list[dict]:
    try:
        rows = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for c in rows if isinstance(rows, list) else []:
        ports = [{"address": p.get("host_ip") or "0.0.0.0", "port": int(p.get("host_port", 0)) + i,
                  "container_port": int(p.get("container_port", 0)) + i, "proto": p.get("protocol", "tcp")}
                 for p in c.get("Ports") or [] for i in range(int(p.get("range") or 1))]
        names = c.get("Names") or []
        out.append({"engine": "podman", "id": str(c.get("Id", ""))[:12], "name": names[0] if names else "", "image": c.get("Image", ""),
                    "status": c.get("Status", "") or c.get("State", ""),
                    "compose_project": (c.get("Labels") or {}).get("com.docker.compose.project", ""), "ports": ports, "pid": c.get("Pid")})
    return out


def containers(runner) -> tuple[list[dict], list[str]]:
    out, notes = [], []
    for engine, name, parse in (("docker", "docker_ps", parse_docker), ("podman", "podman_ps", parse_podman)):
        if not runner.has(engine):
            continue
        code, text, err = runner.privileged(name)
        if code != 0:
            notes.append(f"{engine} ps failed (is the sudo rule there?): {err.strip()[:200]}")
            continue
        out += parse(text)
    return out, notes


# ----------------------------------------------------------------------------- together
def collect(runner, proc: str = PROC) -> dict:
    socks, notes = listeners(runner)
    svc, n2 = units(runner)
    ctrs, n3 = containers(runner)
    pids = {p["pid"] for s in socks for p in s["pids"]} | {u["pid"] for u in svc if u["pid"]}
    procs = processes(pids, proc)
    published = {(p["proto"], p["port"]): c for c in ctrs for p in c["ports"]}
    for s in socks:
        units_here = sorted({procs[p["pid"]]["unit"] for p in s["pids"] if p["pid"] in procs and procs[p["pid"]]["unit"]})
        s["units"] = units_here
        c = published.get((s["proto"], s["port"]))
        if c:
            s["container"] = {"engine": c["engine"], "name": c["name"], "image": c["image"]}
    return {"collected_at": round(time.time()), "listeners": socks, "units": svc, "containers": ctrs,
            "processes": sorted(procs.values(), key=lambda p: p["pid"]), "notes": notes + n2 + n3}
