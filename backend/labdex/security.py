"""Secrets: the app key, encryption of stored credentials, passwords and API tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from werkzeug.security import check_password_hash, generate_password_hash

TOKEN_PREFIX = "ldx_"


def load_secret_key(data_dir: Path) -> str:
    """LABDEX_SECRET_KEY, or a random key created once in the data directory."""
    env = os.environ.get("LABDEX_SECRET_KEY", "")
    if env:
        return env
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_text().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


class Vault:
    """Encrypts integration secrets at rest with a key derived from the app key."""

    def __init__(self, secret_key: str):
        raw = hashlib.sha256(b"labdex-vault:" + secret_key.encode()).digest()
        self._f = Fernet(base64.urlsafe_b64encode(raw))

    def encrypt(self, value: str) -> dict:
        return {"$enc": self._f.encrypt(value.encode()).decode()}

    def decrypt(self, stored) -> str:
        if isinstance(stored, dict) and "$enc" in stored:
            try:
                return self._f.decrypt(stored["$enc"].encode()).decode()
            except InvalidToken:
                return ""  # key changed: the secret has to be entered again
        return stored or ""


# -------------------------------------------------------------------- passwords
MIN_PASSWORD = 10


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(stored: str, password: str) -> bool:
    return bool(stored) and check_password_hash(stored, password)


# ----------------------------------------------------------------------- tokens
def new_token() -> tuple[str, str, str]:
    """Returns (full token shown once, lookup prefix, hash to store)."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full = f"{TOKEN_PREFIX}{prefix}_{secret}"
    return full, prefix, hash_token(full)


def hash_token(full: str) -> str:
    return hashlib.sha256(full.encode()).hexdigest()


def split_token(full: str) -> str | None:
    """The lookup prefix of a token, or None if it doesn't look like ours."""
    if not full.startswith(TOKEN_PREFIX):
        return None
    parts = full[len(TOKEN_PREFIX):].split("_", 1)
    return parts[0] if len(parts) == 2 and len(parts[0]) == 8 else None


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
