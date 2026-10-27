"""A small SNMP client: GET and walks (GETBULK) over UDP, v2c and v3 (USM).

Enough to read neighbour tables from switches without a dependency: BER encoding of the few
types SNMP uses, the v2c community message, and v3 with the user-based security model
(RFC 3414): engine discovery, keys from passphrases localised to the engine, HMAC
authentication (MD5, SHA-1, SHA-256, SHA-512; RFC 3414, RFC 7860) and AES-128 privacy
(RFC 3826). No traps, no SET.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import socket
import struct
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

try:  # AES in CFB mode (RFC 3826) lives on in cryptography's "decrepit" corner
    from cryptography.hazmat.decrepit.ciphers.modes import CFB
except ImportError:  # older cryptography
    from cryptography.hazmat.primitives.ciphers.modes import CFB

# ------------------------------------------------------------------------------- BER
INTEGER, OCTETS, NULL, OID, SEQ = 0x02, 0x04, 0x05, 0x06, 0x30
IPADDR, COUNTER32, GAUGE32, TICKS, OPAQUE, COUNTER64 = 0x40, 0x41, 0x42, 0x43, 0x44, 0x46
NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB = 0x80, 0x81, 0x82
GET, GETNEXT, RESPONSE, GETBULK, REPORT = 0xA0, 0xA1, 0xA2, 0xA5, 0xA8


class SnmpError(Exception):
    pass


def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _len(len(value)) + value


def enc_int(n: int, tag: int = INTEGER) -> bytes:
    length = max(1, (n.bit_length() + 8) // 8)
    return tlv(tag, n.to_bytes(length, "big", signed=True))


def enc_oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.strip(".").split(".")]
    out = bytearray([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(chunk))
    return tlv(OID, bytes(out))


def seq(*items: bytes, tag: int = SEQ) -> bytes:
    return tlv(tag, b"".join(items))


def decode(data: bytes, pos: int = 0):
    """(tag, value bytes, next position)"""
    if pos + 2 > len(data):
        raise SnmpError("truncated message")
    tag = data[pos]
    n = data[pos + 1]
    pos += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(data[pos:pos + k], "big")
        pos += k
    if pos + n > len(data):
        raise SnmpError("truncated message")
    return tag, data[pos:pos + n], pos + n


def items(value: bytes) -> list[tuple[int, bytes]]:
    out, pos = [], 0
    while pos < len(value):
        tag, v, pos = decode(value, pos)
        out.append((tag, v))
    return out


def dec_oid(v: bytes) -> str:
    parts = [v[0] // 40, v[0] % 40]
    n = 0
    for b in v[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(n)
            n = 0
    return ".".join(map(str, parts))


def value_of(tag: int, v: bytes):
    if tag in (INTEGER,):
        return int.from_bytes(v, "big", signed=True)
    if tag in (COUNTER32, GAUGE32, TICKS, COUNTER64):
        return int.from_bytes(v, "big")
    if tag == OCTETS or tag == OPAQUE:
        return v
    if tag == IPADDR:
        return ".".join(str(b) for b in v)
    if tag == OID:
        return dec_oid(v)
    if tag == NULL:
        return None
    if tag in (NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB):
        return _END
    return v


class _EndMarker:
    def __repr__(self):
        return "<no value>"


_END = _EndMarker()


def pdu(kind: int, request_id: int, oids: list[str], a: int = 0, b: int = 0) -> bytes:
    """a/b: error-status/index, or non-repeaters/max-repetitions for GETBULK."""
    binds = seq(*[seq(enc_oid(o), tlv(NULL, b"")) for o in oids])
    return seq(enc_int(request_id), enc_int(a), enc_int(b), binds, tag=kind)


def parse_pdu(raw: bytes) -> tuple[int, int, int, int, list[tuple[str, object]]]:
    """(type, request id, error status, error index, [(oid, value)])"""
    kind, v, _ = decode(raw)
    rid, err, idx, binds = items(v)
    out = []
    for _, vb in items(binds[1]):
        (t1, o), (t2, val) = items(vb)
        out.append((dec_oid(o), value_of(t2, val)))
    return kind, value_of(*rid), value_of(*err), value_of(*idx), out


# ------------------------------------------------------------------------------- USM
AUTH = {  # name: (hash, length of the authentication parameter)
    "md5": (hashlib.md5, 12), "sha": (hashlib.sha1, 12), "sha256": (hashlib.sha256, 24), "sha512": (hashlib.sha512, 48),
}


def password_key(password: str, hash_fn) -> bytes:
    """RFC 3414 A.2: the passphrase repeated to 1 MB, hashed."""
    pw = password.encode()
    if not pw:
        raise SnmpError("an empty passphrase")
    return hash_fn((pw * (1048576 // len(pw) + 1))[:1048576]).digest()


def localize(key: bytes, engine_id: bytes, hash_fn) -> bytes:
    return hash_fn(key + engine_id + key).digest()


class V3Auth:
    def __init__(self, user: str, auth: str = "", auth_key: str = "", priv: str = "", priv_key: str = ""):
        self.user = user
        self.auth = auth.lower().replace("-", "") if auth else ""
        self.priv = priv.lower().replace("-", "").replace("128", "") if priv else ""
        if self.auth and self.auth not in AUTH:
            raise SnmpError(f"authentication {auth}: one of md5, sha, sha256, sha512")
        if self.priv and self.priv != "aes":
            raise SnmpError(f"privacy {priv}: aes (AES-128) only")
        if self.priv and not self.auth:
            raise SnmpError("privacy needs authentication too")
        self._auth_pw, self._priv_pw = auth_key, priv_key
        self._keys: dict[bytes, tuple[bytes, bytes]] = {}

    @property
    def flags(self) -> int:
        return (1 if self.auth else 0) | (2 if self.priv else 0) | 4  # reportable

    def keys(self, engine_id: bytes) -> tuple[bytes, bytes]:
        if engine_id not in self._keys:
            if not self.auth:
                self._keys[engine_id] = (b"", b"")
            else:
                h, _ = AUTH[self.auth]
                ak = localize(password_key(self._auth_pw, h), engine_id, h)
                pk = localize(password_key(self._priv_pw, h), engine_id, h)[:16] if self.priv else b""
                self._keys[engine_id] = (ak, pk)
        return self._keys[engine_id]


ERRORS = {1: "tooBig", 2: "noSuchName", 3: "badValue", 5: "genErr", 6: "noAccess", 16: "authorizationError"}


# ---------------------------------------------------------------------------- client
class Client:
    def __init__(self, host: str, port: int = 161, *, community: str = "public", v3: V3Auth | None = None,
                 timeout: float = 2.0, retries: int = 1):
        self.addr = (host, int(port))
        self.community, self.v3 = community, v3
        self.timeout, self.retries = timeout, retries
        self._rid = int.from_bytes(os.urandom(3), "big")
        self._engine: tuple[bytes, int, int, float] | None = None  # id, boots, time, when learned
        self._sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(timeout)

    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _next_id(self) -> int:
        self._rid = (self._rid + 1) & 0x7FFFFFFF
        return self._rid

    def _exchange(self, packet: bytes, match) -> bytes:
        last = None
        for _ in range(self.retries + 1):
            self._sock.sendto(packet, self.addr)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    self._sock.settimeout(max(0.05, deadline - time.monotonic()))
                    data, _ = self._sock.recvfrom(65535)
                except TimeoutError:
                    break
                try:
                    if match(data):
                        return data
                except SnmpError as exc:
                    last = exc
        hint = (" (with SNMPv3 privacy, a wrong privacy passphrase looks just like this)" if self.v3 and self.v3.priv
                else " (a wrong community looks just like this)" if not self.v3 else "")
        raise last or SnmpError(f"no answer from {self.addr[0]}:{self.addr[1]}{hint}")

    # ------------------------------------------------------------------ v2c
    def _v2c(self, kind, oids, a=0, b=0):
        rid = self._next_id()
        msg = seq(enc_int(1), tlv(OCTETS, self.community.encode()), pdu(kind, rid, oids, a, b))

        def match(data):
            _, v, _ = decode(data)
            ver, comm, p = items(v)
            return parse_pdu(tlv(p[0], p[1]))[1] == rid

        data = self._exchange(msg, match)
        _, v, _ = decode(data)
        p = items(v)[2]
        return parse_pdu(tlv(p[0], p[1]))

    # ------------------------------------------------------------------- v3
    def _discover(self):
        rid = self._next_id()
        header = seq(enc_int(rid), enc_int(65507), tlv(OCTETS, b"\x04"), enc_int(3))
        usm = seq(tlv(OCTETS, b""), enc_int(0), enc_int(0), tlv(OCTETS, b""), tlv(OCTETS, b""), tlv(OCTETS, b""))
        scoped = seq(tlv(OCTETS, b""), tlv(OCTETS, b""), pdu(GET, self._next_id(), []))
        msg = seq(enc_int(3), header, tlv(OCTETS, usm), scoped)
        data = self._exchange(msg, lambda d: True)
        _, v, _ = decode(data)
        _, hdr, sec, _ = items(v)
        eid, boots, etime, *_ = items(decode(sec[1])[1])
        self._engine = (eid[1], value_of(*boots), value_of(*etime), time.monotonic())

    def _v3(self, kind, oids, a=0, b=0, again=True):
        if self._engine is None:
            self._discover()
        eid, boots, etime0, learned = self._engine
        etime = etime0 + int(time.monotonic() - learned)
        auth_key, priv_key = self.v3.keys(eid)
        mid, rid = self._next_id(), self._next_id()
        scoped = seq(tlv(OCTETS, eid), tlv(OCTETS, b""), pdu(kind, rid, oids, a, b))
        priv_params = b""
        if self.v3.priv:
            salt = os.urandom(8)
            iv = struct.pack(">II", boots, etime) + salt
            enc = Cipher(algorithms.AES(priv_key), CFB(iv)).encryptor()
            data_part = tlv(OCTETS, enc.update(scoped) + enc.finalize())
            priv_params = salt
        else:
            data_part = scoped
        alen = AUTH[self.v3.auth][1] if self.v3.auth else 0

        def build(auth_params):
            usm = seq(tlv(OCTETS, eid), enc_int(boots), enc_int(etime), tlv(OCTETS, self.v3.user.encode()),
                      tlv(OCTETS, auth_params), tlv(OCTETS, priv_params))
            header = seq(enc_int(mid), enc_int(65507), tlv(OCTETS, bytes([self.v3.flags])), enc_int(3))
            return seq(enc_int(3), header, tlv(OCTETS, usm), data_part)

        msg = build(b"\x00" * alen)
        if self.v3.auth:
            h = AUTH[self.v3.auth][0]
            msg = build(hmac.new(auth_key, msg, h).digest()[:alen])

        def match(data):
            _, v, _ = decode(data)
            return value_of(*items(items(v)[1][1])[0]) == mid

        data = self._exchange(msg, match)
        _, v, _ = decode(data)
        _, hdr, sec, body = items(v)
        flags = items(hdr[1])[2][1][0]
        usm = items(decode(sec[1])[1])
        r_eid, r_boots, r_time, _, r_auth, r_priv = usm
        if self.v3.auth and flags & 1:  # check the answer's signature
            mac = r_auth[1]
            zeroed = data.replace(mac, b"\x00" * len(mac), 1)
            if not hmac.compare_digest(hmac.new(auth_key, zeroed, AUTH[self.v3.auth][0]).digest()[:alen], mac):
                raise SnmpError("the answer's signature doesn't match: wrong authentication passphrase?")
        if flags & 2:
            iv = struct.pack(">II", value_of(*r_boots), value_of(*r_time)) + r_priv[1]
            dec = Cipher(algorithms.AES(priv_key), CFB(iv)).decryptor()
            plain = dec.update(body[1]) + dec.finalize()
            _, sv, _ = decode(plain)
        else:
            sv = body[1]
        parts = items(sv)
        p = parts[2]
        res = parse_pdu(tlv(p[0], p[1]))
        if res[0] == REPORT:
            oid = res[4][0][0] if res[4] else ""
            if oid.startswith("1.3.6.1.6.3.15.1.1.2") and again:  # notInTimeWindow: learn the clock, once more
                self._engine = (r_eid[1], value_of(*r_boots), value_of(*r_time), time.monotonic())
                return self._v3(kind, oids, a, b, again=False)
            reasons = {"1.3.6.1.6.3.15.1.1.3": "unknown user", "1.3.6.1.6.3.15.1.1.5": "wrong authentication passphrase or protocol",
                       "1.3.6.1.6.3.15.1.1.6": "wrong privacy passphrase or protocol", "1.3.6.1.6.3.15.1.1.1": "security level not supported",
                       "1.3.6.1.6.3.15.1.1.4": "unknown engine"}
            raise SnmpError(f"the agent refused: {next((t for k, t in reasons.items() if oid.startswith(k)), oid or 'a report')}")
        return res

    # ----------------------------------------------------------------- calls
    def _call(self, kind, oids, a=0, b=0):
        res = self._v3(kind, oids, a, b) if self.v3 else self._v2c(kind, oids, a, b)
        if res[2]:
            raise SnmpError(f"the agent said {ERRORS.get(res[2], f'error {res[2]}')}"
                            + (" (the user may not read this, or needs a higher security level)" if res[2] in (6, 16) else ""))
        return res[4]

    def get(self, *oids: str) -> dict[str, object]:
        return {o: v for o, v in self._call(GET, list(oids)) if v is not _END}

    def walk(self, root: str, max_rows: int = 20000, bulk: int = 25) -> list[tuple[str, object]]:
        """Every (oid, value) under root, in order."""
        root = root.strip(".")
        out, cur = [], root
        while len(out) < max_rows:
            binds = self._call(GETBULK, [cur], 0, bulk)
            if not binds:
                break
            done = False
            for oid, val in binds:
                if val is _END or not (oid + ".").startswith(root + "."):
                    done = True
                    break
                if out and _oid_key(oid) <= _oid_key(out[-1][0]):
                    raise SnmpError(f"the agent went backwards at {oid}")
                out.append((oid, val))
            if done:
                break
            cur = binds[-1][0]
        return out


def _oid_key(oid: str) -> tuple[int, ...]:
    return tuple(int(x) for x in oid.split("."))
