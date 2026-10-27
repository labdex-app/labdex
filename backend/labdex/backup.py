"""Backups: the database plus the inventory YAML, as .tar.gz, optionally encrypted
with a passphrase (scrypt + AES-256-GCM).

The database is in it twice on SQLite: labdex.db (a consistent snapshot of the
file) and database.json (a portable dump, see dbdump.py). On PostgreSQL only the
dump; it restores into either backend.

What a backup does NOT contain: secret.key. Secrets inside the database stay
encrypted with it, so a stolen backup doesn't reveal API keys or SSH keys, and
restoring them needs the original secret.key (keep it somewhere safe, offline).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sqlite3
import tarfile
import tempfile
from datetime import datetime, UTC
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"LABDEX-BACKUP-1\n"
PREFIX = "labdex-backup-"


class BackupError(Exception):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def encrypt(data: bytes, passphrase: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise BackupError("not an encrypted labdex backup")
    body = blob[len(MAGIC):]
    salt, nonce, ct = body[:16], body[16:28], body[28:]
    try:
        return AESGCM(_key(passphrase, salt)).decrypt(nonce, ct, MAGIC)
    except Exception as exc:  # InvalidTag
        raise BackupError("wrong passphrase, or the file was modified") from exc


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def snapshot_db(dest: Path) -> bool:
    """Copy the live SQLite database with the online backup API (consistent while running)."""
    from .models import db
    if db.engine.url.get_backend_name() != "sqlite":
        return False
    raw = db.engine.raw_connection()
    try:
        src = raw.driver_connection
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        raw.close()
    return True


def create(version: str, passphrase: str = "") -> tuple[str, bytes, dict]:
    """(filename, bytes, manifest)"""
    from . import inventory
    now = datetime.now(UTC)
    files: dict[str, bytes] = {"inventory.yaml": inventory.export_yaml().encode()}
    with tempfile.TemporaryDirectory(prefix="labdex-backup-") as tmp:
        dbfile = Path(tmp) / "labdex.db"
        if snapshot_db(dbfile):
            files["labdex.db"] = dbfile.read_bytes()
    from . import dbdump
    from .models import db
    with db.engine.connect() as conn:
        files["database.json"] = dbdump.dumps(conn)
    manifest = {
        "labdex": version, "created": now.isoformat(), "host": socket.gethostname(),
        "files": {n: {"size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in files.items()},
        "secrets": "encrypted with the instance's secret.key, which is not in this backup",
    }
    files["manifest.json"] = json.dumps(manifest, indent=2).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), int(now.timestamp()), 0o600
            tar.addfile(info, io.BytesIO(data))
    data = buf.getvalue()
    name = f"{PREFIX}{now.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    if passphrase:
        data, name = encrypt(data, passphrase), name + ".enc"
    manifest["encrypted"] = bool(passphrase)
    return name, data, manifest


MAX_UNPACKED = 4 * 1024 ** 3  # a gzip bomb stops here


def open_archive(blob: bytes, passphrase: str = "") -> dict[str, bytes]:
    """Decrypt if needed and return the files, checking the manifest hashes."""
    if is_encrypted(blob):
        if not passphrase:
            raise BackupError("this backup is encrypted; give the passphrase")
        blob = decrypt(blob, passphrase)
    out, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar:
            if not m.isfile() or "/" in m.name or m.name.startswith(".") or len(out) >= 32:
                raise BackupError(f"unexpected entry {m.name!r} in the archive")
            data = tar.extractfile(m).read(MAX_UNPACKED + 1 - total)
            total += len(data)
            if total > MAX_UNPACKED:
                raise BackupError("the archive unpacks to more than a backup can hold")
            out[m.name] = data
    manifest = json.loads(out.get("manifest.json", b"{}"))
    for n, meta in (manifest.get("files") or {}).items():
        if hashlib.sha256(out.get(n, b"")).hexdigest() != meta["sha256"]:
            raise BackupError(f"{n} doesn't match its checksum")
    return out
