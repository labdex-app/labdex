"""The KV vault: the lab's own secrets (root passwords, IPMI logins, Wi-Fi keys, recovery codes).

Envelope encryption, sealed start:

    passphrase --scrypt--> key-encryption key --AES-GCM--> master key (32 bytes, random)
    master key --AES-GCM--> per-version data key --AES-GCM--> the entry's values (JSON)

Only the wrapped master key is stored. After a restart the vault is *sealed*: an administrator
types the passphrase, labdex unwraps the master key and keeps it in this process's memory
until it's sealed again or the process ends. A database dump or a backup alone reveals nothing,
and neither does secret.key (that one protects integration settings, not the vault).

Every ciphertext is bound to what it belongs to (associated data): a data key to its entry and
version, the values to the same, so blobs can't be swapped between entries unnoticed.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .models import VaultEntry, VaultKey, VaultVersion, db, utcnow

PATH_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}(/[a-z0-9][a-z0-9._-]{0,63}){0,7}$")
FIELD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MIN_PASSPHRASE = 12
KEEP_VERSIONS = 10
SCRYPT = (2 ** 15, 8, 1)  # n, r, p: ~32 MiB and a few hundred ms, once per unseal
MASTER_AAD = b"labdex-vault-master-v1"

_master: bytes | None = None
_lock = threading.Lock()
_tries: list[float] = []  # failed unseals in this process, for throttling


class VaultError(Exception):
    """Something the caller should be told (wrong passphrase, sealed, …)."""

    def __init__(self, message: str, status: int = 422, field: str = ""):
        super().__init__(message)
        self.status, self.field = status, field


class Sealed(VaultError):
    def __init__(self):
        super().__init__("the vault is sealed: an administrator unseals it with the passphrase", 423)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _seal_with(key: bytes, plain: bytes, aad: bytes) -> str:
    nonce = os.urandom(12)
    return _b64(nonce + AESGCM(key).encrypt(nonce, plain, aad))


def _open_with(key: bytes, blob: str, aad: bytes) -> bytes:
    raw = _unb64(blob)
    return AESGCM(key).decrypt(raw[:12], raw[12:], aad)


def _kek(passphrase: str, salt: bytes, kdf: str) -> bytes:
    _, n, r, p = kdf.split(":")
    return Scrypt(salt=salt, length=32, n=int(n), r=int(r), p=int(p)).derive(passphrase.encode())


# ------------------------------------------------------------------ the master key
def _row() -> VaultKey | None:
    return db.session.scalar(db.select(VaultKey).order_by(VaultKey.id))


def status() -> dict:
    return {"initialized": _row() is not None, "sealed": _master is None}


def init(passphrase: str) -> None:
    global _master
    if _row() is not None:
        raise VaultError("the vault is set up already", 409)
    _check_passphrase(passphrase)
    master, salt = os.urandom(32), os.urandom(16)
    kdf = "scrypt:{}:{}:{}".format(*SCRYPT)
    db.session.add(VaultKey(salt=_b64(salt), kdf=kdf, wrapped=_seal_with(_kek(passphrase, salt, kdf), master, MASTER_AAD)))
    db.session.flush()
    with _lock:
        _master = master


def _check_passphrase(p: str) -> None:
    if len(p or "") < MIN_PASSPHRASE:
        raise VaultError(f"at least {MIN_PASSPHRASE} characters: a few words are easy to remember and hard to guess", field="passphrase")


def _unwrap(passphrase: str) -> bytes:
    row = _row()
    if row is None:
        raise VaultError("the vault isn't set up yet", 409)
    now = time.monotonic()
    _tries[:] = [t for t in _tries if now - t < 60]
    if len(_tries) >= 5:
        raise VaultError("too many wrong passphrases; wait a minute", 429)
    try:
        return _open_with(_kek(passphrase or "", _unb64(row.salt), row.kdf), row.wrapped, MASTER_AAD)
    except InvalidTag:
        _tries.append(now)
        raise VaultError("that's not the passphrase", 403, field="passphrase") from None


def unseal(passphrase: str) -> None:
    global _master
    master = _unwrap(passphrase)
    with _lock:
        _master = master


def seal() -> None:
    global _master
    with _lock:
        _master = None


def change_passphrase(old: str, new: str) -> None:
    """Wraps the same master key with the new passphrase: the entries stay as they are."""
    global _master
    master = _unwrap(old)
    _check_passphrase(new)
    row = _row()
    salt = os.urandom(16)
    kdf = "scrypt:{}:{}:{}".format(*SCRYPT)
    row.salt, row.kdf, row.wrapped, row.rotated_at = _b64(salt), kdf, _seal_with(_kek(new, salt, kdf), master, MASTER_AAD), utcnow()
    with _lock:
        _master = master


def _key() -> bytes:
    if _master is None:
        raise Sealed()
    return _master


# ------------------------------------------------------------------ entries
def _aad(e: VaultEntry, version: int, part: str) -> bytes:
    return f"labdex-vault:{e.uid}:{version}:{part}".encode()


def _write(e: VaultEntry, values: dict, actor: str) -> None:
    """A new version of the entry with these values."""
    master = _key()
    version = (e.version or 0) + 1 if e.id else 1
    if e.id is None:
        db.session.add(e)
        db.session.flush()
    data_key = os.urandom(32)
    db.session.add(VaultVersion(entry_id=e.id, version=version, fields=list(values),
                                wrapped_key=_seal_with(master, data_key, _aad(e, version, "key")),
                                ciphertext=_seal_with(data_key, json.dumps(values).encode(), _aad(e, version, "values")),
                                created_by=actor))
    e.version, e.fields, e.updated_by, e.updated_at = version, list(values), actor, utcnow()
    old = db.session.scalars(db.select(VaultVersion).where(VaultVersion.entry_id == e.id, VaultVersion.version <= version - KEEP_VERSIONS))
    for v in old:
        db.session.delete(v)
    db.session.flush()


def read(e: VaultEntry, version: int | None = None) -> dict:
    """The values of the entry (or of an older version). Only for a reveal the caller is allowed."""
    master = _key()
    v = db.session.scalar(db.select(VaultVersion).where(VaultVersion.entry_id == e.id, VaultVersion.version == (version or e.version)))
    if v is None:
        raise VaultError(f"no such version (only the last {KEEP_VERSIONS} are kept)", 404)
    try:
        data_key = _open_with(master, v.wrapped_key, _aad(e, v.version, "key"))
        return json.loads(_open_with(data_key, v.ciphertext, _aad(e, v.version, "values")))
    except InvalidTag:
        raise VaultError("this entry doesn't decrypt: it was changed outside labdex", 500) from None


def facts_for(path: str, host):
    """What rights are matched against: an entry of a host like that host (zone, tags, name …),
    plus its path as a name; any other entry by its path (name:wifi/*)."""
    from . import rbac, store
    if host is not None:
        f = rbac.host_facts(store.host_access(host))
        f.names = set(f.names) | {path}
        return f
    return rbac.named_facts(path)


def facts(e: VaultEntry):
    return facts_for(e.path, e.host)


def clean_path(path: str) -> str:
    p = str(path or "").strip().strip("/").lower()
    if not PATH_RE.match(p):
        raise VaultError("like hosts/srv-pm-pve-01/root or wifi/iot: lowercase letters, digits, . _ -, up to 8 parts", field="path")
    return p


def _clean_values(fields: dict, keep: dict) -> dict:
    """fields {name: value}; a value of None keeps the stored one."""
    if not isinstance(fields, dict) or not fields:
        raise VaultError("at least one field, like password", field="fields")
    if len(fields) > 40:
        raise VaultError("at most 40 fields", field="fields")
    out = {}
    for k, v in fields.items():
        if not FIELD_RE.match(str(k)):
            raise VaultError(f"field '{k}': letters, digits and _ . - (at most 64)", field="fields")
        if v is None:
            if k not in keep:
                raise VaultError(f"field '{k}' has no value yet", field="fields")
            v = keep[k]
        v = str(v)
        if len(v) > 16000:
            raise VaultError(f"field '{k}': at most 16000 characters", field="fields")
        out[str(k)] = v
    return out


def create(path: str, fields: dict, actor: str, host=None, description: str = "") -> VaultEntry:
    path = clean_path(path)
    if db.session.scalar(db.select(VaultEntry).where(VaultEntry.path == path)) is not None:
        raise VaultError(f"{path} exists already", 409, field="path")
    _key()
    e = VaultEntry(path=path, host=host, description=str(description or "")[:2000])
    _write(e, _clean_values(fields, {}), actor)
    return e


def update(e: VaultEntry, actor: str, path: str | None = None, fields: dict | None = None, host=False, description: str | None = None) -> None:
    """host=False: leave as is. fields: the whole new set (None as a value keeps that field's value);
    fields left out are dropped. Changing values needs the vault unsealed; path, host and
    description don't."""
    if path is not None:
        p = clean_path(path)
        other = db.session.scalar(db.select(VaultEntry).where(VaultEntry.path == p))
        if other is not None and other is not e:
            raise VaultError(f"{p} exists already", 409, field="path")
        e.path = p
    if host is not False:
        e.host = host
    if description is not None:
        e.description = str(description)[:2000]
    if fields is not None:
        current = read(e) if any(v is None for v in fields.values()) else {}
        _write(e, _clean_values(fields, current), actor)
    else:
        e.updated_by, e.updated_at = actor, utcnow()
