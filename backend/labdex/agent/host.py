"""What the agent reads about its own host: facts (once in a while) and metrics (every heartbeat).

Everything comes from /proc, /etc/os-release and os.statvfs: no commands, no root.
"""
from __future__ import annotations

import os
import platform
import socket
import time

PROC = "/proc"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def os_release(path: str = "/etc/os-release") -> dict:
    out = {}
    for line in _read(path).splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def meminfo(proc: str = PROC) -> dict:
    out = {}
    for line in _read(f"{proc}/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0].rstrip(":")] = int(parts[1]) * 1024
    return out


def cpu_times(proc: str = PROC) -> tuple[int, int]:
    """(busy, total) jiffies since boot, from the first line of /proc/stat."""
    for line in _read(f"{proc}/stat").splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)  # idle + iowait
            return sum(v) - idle, sum(v)
    return 0, 0


def uptime(proc: str = PROC) -> float:
    try:
        return float(_read(f"{proc}/uptime").split()[0])
    except (IndexError, ValueError):
        return 0.0


def mounts(proc: str = PROC) -> list[str]:
    """Mount points of real file systems (no tmpfs, overlay, proc …)."""
    real = ("ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "vfat", "f2fs", "jfs", "reiserfs", "bcachefs")
    out = []
    for line in _read(f"{proc}/mounts").splitlines():
        parts = line.split()
        path = parts[1].replace("\\040", " ") if len(parts) >= 3 else ""
        # a single bind-mounted file (containers: /etc/hosts) is no disk
        if path and parts[2] in real and path not in out and os.path.isdir(path):
            out.append(path)
    return out or ["/"]


def disk(path: str) -> dict:
    try:
        st = os.statvfs(path)
    except OSError:
        return {}
    total = st.f_blocks * st.f_frsize
    return {"mount": path, "total": total, "used": total - st.f_bfree * st.f_frsize, "free": st.f_bavail * st.f_frsize}


def net_bytes(proc: str = PROC) -> tuple[int, int]:
    rx = tx = 0
    for line in _read(f"{proc}/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        v = rest.split()
        if name.strip() != "lo" and len(v) >= 9:
            rx, tx = rx + int(v[0]), tx + int(v[8])
    return rx, tx


def facts() -> dict:
    rel = os_release()
    mem = meminfo()
    return {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "os": rel.get("PRETTY_NAME") or platform.system(),
        "os_id": rel.get("ID", ""),
        "os_version": rel.get("VERSION_ID", ""),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpus": os.cpu_count() or 0,
        "memory": mem.get("MemTotal", 0),
        "disks": [d for d in (disk(m) for m in mounts()) if d],
        "python": platform.python_version(),
        "user": _user(),
        "root": os.geteuid() == 0,
        "boot_time": round(time.time() - uptime()),
    }


def _user() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError):
        return str(os.geteuid())


class Metrics:
    """Numbers for each heartbeat. CPU use is over the time since the last call."""

    def __init__(self, proc: str = PROC):
        self.proc = proc
        self._cpu = cpu_times(proc)

    def collect(self) -> dict:
        busy, total = cpu_times(self.proc)
        b0, t0 = self._cpu
        self._cpu = (busy, total)
        mem = meminfo(self.proc)
        try:
            load = os.getloadavg()
        except OSError:
            load = (0.0, 0.0, 0.0)
        root = disk("/")
        rx, tx = net_bytes(self.proc)
        return {
            "cpu_percent": round(100.0 * (busy - b0) / (total - t0), 1) if total > t0 else 0.0,
            "load1": round(load[0], 2), "load5": round(load[1], 2), "load15": round(load[2], 2),
            "mem_total": mem.get("MemTotal", 0), "mem_available": mem.get("MemAvailable", 0),
            "swap_total": mem.get("SwapTotal", 0), "swap_free": mem.get("SwapFree", 0),
            "root_total": root.get("total", 0), "root_used": root.get("used", 0),
            "uptime": round(uptime(self.proc)),
            "net_rx_bytes": rx, "net_tx_bytes": tx,
        }
