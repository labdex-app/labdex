"""Landlock: an unprivileged process locks itself (and everything it starts) out of every
file except the ones it names. Linux 5.13+; labdex uses it for isolated plugins.

    abi = landlock.abi()            # 0 = not available
    landlock.restrict(read=[...], write=[...], files_rw=[...])

restrict() applies to the calling process and can't be undone.
"""
from __future__ import annotations

import ctypes
import os
import platform

# syscall numbers are the same on every architecture that has Landlock (generic table)
SYS_CREATE, SYS_ADD_RULE, SYS_RESTRICT = 444, 445, 446
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE, MAKE_CHAR, MAKE_DIR = 1 << 4, 1 << 5, 1 << 6, 1 << 7
MAKE_REG, MAKE_SOCK, MAKE_FIFO, MAKE_BLOCK, MAKE_SYM = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL = 1 << 0, 1 << 1

FILE_ONLY = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV  # rights that apply to a file itself
READ = EXECUTE | READ_FILE | READ_DIR


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class _Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def abi() -> int:
    """The Landlock ABI version the kernel offers; 0 when there is none (or not Linux)."""
    if platform.system() != "Linux":
        return 0
    try:
        r = _libc().syscall(SYS_CREATE, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION))
    except (OSError, AttributeError):
        return 0
    return max(int(r), 0)


def _handled(v: int) -> int:
    fs = EXECUTE | WRITE_FILE | READ_FILE | READ_DIR | REMOVE_DIR | REMOVE_FILE | MAKE_CHAR | MAKE_DIR | MAKE_REG \
        | MAKE_SOCK | MAKE_FIFO | MAKE_BLOCK | MAKE_SYM
    if v >= 2:
        fs |= REFER
    if v >= 3:
        fs |= TRUNCATE
    if v >= 5:
        fs |= IOCTL_DEV
    return fs


def restrict(read: list[str], write: list[str], files_rw: list[str] = ()) -> int:
    """Allow reading (and running) below `read`, everything below `write`, reading and writing
    the single files in `files_rw` (like /dev/null), and nothing else. Returns the ABI used.
    Raises OSError if the kernel refuses."""
    v = abi()
    if v <= 0:
        raise OSError("Landlock isn't available in this kernel")
    libc = _libc()
    handled = _handled(v)
    attr = _Ruleset(handled, 0, (SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL) if v >= 6 else 0)
    size = 24 if v >= 6 else (16 if v >= 4 else 8)
    fd = libc.syscall(SYS_CREATE, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset: " + os.strerror(ctypes.get_errno()))
    try:
        def add(path: str, rights: int):
            try:
                pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                return  # a path that isn't there needs no rule
            try:
                if not os.path.isdir(path):
                    rights &= FILE_ONLY
                rule = _PathBeneath(rights & handled, pfd)
                if libc.syscall(SYS_ADD_RULE, ctypes.c_int(fd), ctypes.c_int(RULE_PATH_BENEATH), ctypes.byref(rule), ctypes.c_uint32(0)) < 0:
                    err = ctypes.get_errno()
                    raise OSError(err, f"landlock_add_rule {path}: {os.strerror(err)}")
            finally:
                os.close(pfd)

        for p in read:
            add(p, READ)
        for p in write:
            add(p, handled)
        for p in files_rw:
            add(p, READ_FILE | WRITE_FILE | TRUNCATE | IOCTL_DEV)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS)")
        if libc.syscall(SYS_RESTRICT, ctypes.c_int(fd), ctypes.c_uint32(0)) < 0:
            err = ctypes.get_errno()
            raise OSError(err, "landlock_restrict_self: " + os.strerror(err))
    finally:
        os.close(fd)
    return v
