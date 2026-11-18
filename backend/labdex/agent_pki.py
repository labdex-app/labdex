"""Client certificates for the host agents: mTLS on top of the per-host key.

labdex is a small certificate authority for its own agents, and for nothing else. At enrolment the
agent makes a key pair on the host and sends a certificate request with the token; labdex signs a
client certificate for it (CN = the agent id, a year) and keeps its SHA-256. On the link the agent
presents the certificate in the TLS handshake, and labdex checks it on top of the key: issued by
this CA, in date, CN is this agent, and the fingerprint is the one on record. Revoking the agent
makes both worthless at once, so no revocation list is needed. Thirty days before it runs out the
hub asks the agent for a new request over the link.

Where the certificate comes from depends on who terminates TLS:

- labdex itself (gunicorn ``--certfile`` with ``--cert-reqs 1 --ca-certs agent-ca.pem``, or the
  development server): read from the connection;
- a reverse proxy that asks for client certificates: it passes the certificate in the header named
  by ``LABDEX_AGENT_CERT_HEADER``, which counts only on requests from ``LABDEX_TRUSTED_PROXIES``.
  PEM (URL-escaped or not) and base64 DER both work.

``LABDEX_AGENT_MTLS``: ``off`` (keys only), ``optional`` (the default: agents get certificates, and
a certificate that is presented must be right) or ``required`` (no link without one).
"""
from __future__ import annotations

import base64
import hashlib
import os
import ssl
import urllib.parse
from datetime import datetime, timedelta, UTC
from pathlib import Path

from flask import current_app, request

MODES = ("off", "optional", "required")
CA_KEY = "agent-ca.key"
CA_CERT = "agent-ca.pem"
CA_YEARS = 20
CERT_DAYS = 365
RENEW_BEFORE = timedelta(days=30)
MAX_CERT = 16 * 1024


class CertError(ValueError):
    """A certificate or request that won't do; the text says why."""


def mode() -> str:
    m = str(current_app.config.get("LABDEX_AGENT_MTLS") or "optional").strip().lower()
    return m if m in MODES else "required"  # a typo must not switch the check off


def _now() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------------------------- the CA
def _paths() -> tuple[Path, Path]:
    d = Path(current_app.config["LABDEX_DATA_DIR"])
    return d / CA_KEY, d / CA_CERT


def ca():
    """(key, certificate) of the agents' CA, made once in the data folder (the key with mode 0600)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cached = current_app.extensions["labdex"].get("agent_ca")
    if cached is not None:
        return cached
    key_path, cert_path = _paths()
    if not key_path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "labdex"),
                          x509.NameAttribute(NameOID.COMMON_NAME, "labdex agent CA")])
        now = _now()
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=365 * CA_YEARS))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                             data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                             encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .sign(key, hashes.SHA256()))
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another process made it first: use that one
            return ca()
        # the certificate first, so a reader that sees the key also finds its certificate
        tmp = cert_path.with_suffix(".tmp")
        tmp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        os.replace(tmp, cert_path)
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    else:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    current_app.extensions["labdex"]["agent_ca"] = (key, cert)
    return key, cert


def ca_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    return ca()[1].public_bytes(serialization.Encoding.PEM).decode()


# ----------------------------------------------------------------------------- issuing
def issue(csr_pem: str, agent_id: str) -> tuple[str, str, datetime]:
    """Sign a client certificate for the agent from its request. Only the request's public key is
    used: the subject is always CN=<agent id>. Returns (PEM, SHA-256 of the DER, not after, naive UTC)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if not isinstance(csr_pem, str) or not csr_pem.strip() or len(csr_pem) > MAX_CERT:
        raise CertError("no certificate request")
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
    except ValueError as e:
        raise CertError("the certificate request can't be read") from e
    if not csr.is_signature_valid:
        raise CertError("the certificate request isn't signed by its own key")
    pub = csr.public_key()
    if not (isinstance(pub, ec.EllipticCurvePublicKey) and pub.curve.name in ("secp256r1", "secp384r1")):
        raise CertError("the certificate request needs an EC P-256 or P-384 key")
    ca_key, ca_cert = ca()
    now = _now()
    not_after = now + timedelta(days=CERT_DAYS)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "labdex agent"),
                                     x509.NameAttribute(NameOID.COMMON_NAME, agent_id)]))
            .issuer_name(ca_cert.subject).public_key(pub).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    return cert.public_bytes(serialization.Encoding.PEM).decode(), hashlib.sha256(der).hexdigest(), not_after.replace(tzinfo=None)


def needs_renewal(not_after: datetime | None, now: datetime | None = None) -> bool:
    now = now or _now().replace(tzinfo=None)
    return not_after is None or not_after - now < RENEW_BEFORE


# ----------------------------------------------------------------------------- checking
def _trusted_proxy() -> bool:
    from .auth import _trusted_proxy as trusted
    return trusted()


def _parse(value: str) -> bytes:
    """The certificate from a proxy header: PEM (URL-escaped as nginx's $ssl_client_escaped_cert,
    or with spaces for newlines) or base64 DER (Caddy, Traefik). The first certificate of a chain."""
    text = urllib.parse.unquote(value.strip()) if "%" in value else value.strip()
    if "BEGIN CERTIFICATE" in text:
        body = text.split("BEGIN CERTIFICATE-----", 1)[1].split("-----END", 1)[0]
    else:
        body = text.split(",", 1)[0]
    try:
        return base64.b64decode("".join(body.split()), validate=True)
    except ValueError as e:
        raise CertError("the client certificate header can't be read") from e


def presented() -> bytes | None:
    """The DER client certificate of this request, or None. A header counts only from a trusted proxy."""
    env = request.environ
    sock = env.get("gunicorn.socket")
    if isinstance(sock, ssl.SSLSocket):
        der = sock.getpeercert(binary_form=True)
        if der:
            return der
    pem = env.get("SSL_CLIENT_CERT")  # werkzeug's server and mod_wsgi put it here; clients can't
    if isinstance(pem, str) and "BEGIN CERTIFICATE" in pem:
        return ssl.PEM_cert_to_DER_cert(pem.strip())
    header = str(current_app.config.get("LABDEX_AGENT_CERT_HEADER") or "")
    if header and _trusted_proxy():
        value = request.headers.get(header, "")
        if value.strip() and len(value) <= MAX_CERT * 2:
            return _parse(value)
    return None


def check(agent, der: bytes | None) -> str | None:
    """Why this certificate doesn't belong to this agent, or None when it does.
    No certificate is fine unless the mode is required."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    if der is None:
        return "this labdex requires a client certificate" if mode() == "required" else None
    try:
        cert = x509.load_der_x509_certificate(der)
    except ValueError:
        return "the client certificate can't be read"
    try:
        cert.verify_directly_issued_by(ca()[1])
    except Exception:  # wrong issuer, or a signature that doesn't verify
        return "the client certificate isn't from this labdex"
    now = _now()
    if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
        return "the client certificate is out of date"
    cn = [a.value for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    if cn != [agent.id]:
        return "the client certificate is for another agent"
    sha = hashlib.sha256(der).hexdigest()
    if sha not in {agent.cert_sha256, agent.cert_prev_sha256} - {""}:
        return "the client certificate isn't the one on record for this agent"
    return None
