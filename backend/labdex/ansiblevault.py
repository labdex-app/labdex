"""Ansible Vault and secret references in Ansible variables.

Feature and host variables may point at a secret in the vault instead of holding it:

    grafana_admin_password: {$secret: vault:apps/grafana#admin}
    db_password: {$secret: grafana-db}          # the older form: vault:global/grafana-db#value

labdex never writes the value into the inventory, the YAML export or the history.
When labdex runs Ansible itself, the references are filled in for that run only
(in a private temporary directory). The Ansible inventory export turns them into
`!vault` strings when the export has a vault password, so a playbook run outside
labdex can use them with the same password.

The vault format is Ansible's own (1.1, or 1.2 with a vault id): PBKDF2-SHA256 with
10,000 rounds derives an AES-256 key, an HMAC-SHA256 key and the CTR nonce; the
padded text is encrypted with AES-256-CTR and authenticated with the HMAC.
"""
from __future__ import annotations

import binascii
import hmac as _hmac
import os
from hashlib import sha256

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

REF = "$secret"


class VaultError(Exception):
    pass


def _keys(password: bytes, salt: bytes) -> tuple[bytes, bytes, bytes]:
    km = PBKDF2HMAC(algorithm=hashes.SHA256(), length=80, salt=salt, iterations=10000).derive(password)
    return km[:32], km[32:64], km[64:80]


def encrypt(plaintext: str, password: str, vault_id: str = "") -> str:
    """Encrypt like `ansible-vault encrypt_string`. Returns the vault text (header + hex lines)."""
    salt = os.urandom(32)
    k1, k2, iv = _keys(password.encode(), salt)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(k1), modes.CTR(iv)).encryptor()
    ct = enc.update(data) + enc.finalize()
    mac = _hmac.new(k2, ct, sha256).hexdigest().encode()
    body = binascii.hexlify(b"\n".join([binascii.hexlify(salt), mac, binascii.hexlify(ct)])).decode()
    header = f"$ANSIBLE_VAULT;1.2;AES256;{vault_id}" if vault_id else "$ANSIBLE_VAULT;1.1;AES256"
    return header + "\n" + "\n".join(body[i:i + 80] for i in range(0, len(body), 80))


def decrypt(vaulttext: str, password: str) -> str:
    lines = vaulttext.strip().splitlines()
    if not lines or not lines[0].startswith("$ANSIBLE_VAULT;"):
        raise VaultError("not vault text")
    try:
        inner = binascii.unhexlify("".join(x.strip() for x in lines[1:]))
        salt_hex, mac, ct_hex = inner.split(b"\n", 2)
        salt, ct = binascii.unhexlify(salt_hex), binascii.unhexlify(ct_hex)
    except (ValueError, binascii.Error) as exc:
        raise VaultError("damaged vault text") from exc
    k1, k2, iv = _keys(password.encode(), salt)
    if not _hmac.compare_digest(_hmac.new(k2, ct, sha256).hexdigest().encode(), mac):
        raise VaultError("wrong vault password")
    dec = Cipher(algorithms.AES(k1), modes.CTR(iv)).decryptor()
    data = dec.update(ct) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    return (unpad.update(data) + unpad.finalize()).decode()


# ------------------------------------------------------------- secret references
def is_ref(v) -> bool:
    return isinstance(v, dict) and len(v) == 1 and REF in v and isinstance(v[REF], str)


def refs(obj) -> set[str]:
    """Names of the secrets a structure points at."""
    out = set()
    if is_ref(obj):
        out.add(obj[REF])
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= refs(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= refs(v)
    return out


def replace(obj, fn):
    """A copy of obj with every reference replaced by fn(name)."""
    if is_ref(obj):
        return fn(obj[REF])
    if isinstance(obj, dict):
        return {k: replace(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace(v, fn) for v in obj]
    return obj


def missing(obj) -> list[str]:
    from . import secretstore
    return sorted(n for n in refs(obj) if not secretstore.exists(n))


def reveal_all(obj, seen: set | None = None):
    """For a run inside labdex only: references → their values (collected in `seen`, to mask them in logs)."""
    from . import secretstore

    def val(name):
        try:
            v = secretstore.reveal(name)
        except secretstore.RefError as exc:
            raise VaultError(str(exc)) from None
        if v is None:
            raise VaultError(f"secret {secretstore.label(name)} isn't in the vault")
        if seen is not None:
            seen.add(v)
        return v
    return replace(obj, val)


def masker(values):
    """A function that hides these values in a line of output."""
    vals = sorted({v for v in values if isinstance(v, str) and len(v) >= 4}, key=len, reverse=True)
    if not vals:
        return lambda line: line
    import re
    rx = re.compile("|".join(re.escape(v) for v in vals))
    return lambda line: rx.sub("********", line)


class Vaulted(str):
    """A value that YAML dumps as `!vault |`."""


def vault_all(obj, password: str, vault_id: str = ""):
    """For exports: references → `!vault` values encrypted with the export's vault password."""
    from . import secretstore

    def val(name):
        try:
            v = secretstore.reveal(name)
        except secretstore.RefError as exc:
            raise VaultError(str(exc)) from None
        if v is None:
            raise VaultError(f"secret {secretstore.label(name)} isn't in the vault")
        return Vaulted(encrypt(v, password, vault_id))
    return replace(obj, val)

