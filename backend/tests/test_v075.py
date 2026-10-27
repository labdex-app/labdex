"""v0.7.5: certificate expiry."""
import datetime as dt
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from labdex import widgets
from labdex.models import Notification, db

from .test_plugins import make_instance, run


def _tls_server(tmp_path, days_left: float, name="svc.lab.example.net"):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=30))
            .not_valid_after(now + dt.timedelta(days=days_left))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False).sign(key, hashes.SHA256()))
    cf, kf = tmp_path / f"c{days_left}.pem", tmp_path / f"k{days_left}.pem"
    cf.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kf.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cf, kf)
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    stop = threading.Event()

    def loop():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                continue
            try:
                with ctx.wrap_socket(c, server_side=True) as t:
                    t.recv(1)
            except (ssl.SSLError, OSError):
                pass
    threading.Thread(target=loop, daemon=True).start()
    return srv.getsockname()[1], lambda: (stop.set(), srv.close())


@pytest.fixture
def servers(tmp_path):
    p1, stop1 = _tls_server(tmp_path, 5)
    p2, stop2 = _tls_server(tmp_path, 200, "fine.lab.example.net")
    yield p1, p2
    stop1()
    stop2()


def test_certificates(admin, servers):
    p1, p2 = servers
    admin.post("/api/notification-channels", json={"name": "hook", "kind": "webhook", "settings": {"url": "http://127.0.0.1:9/x"},
                                                   "events": ["plugin.certificates.*"]})
    make_instance(admin, "certificates", "certs", {"extra": [f"127.0.0.1:{p1}", f"https://localhost:{p2}"], "timeout": 1})
    spec = admin.application.extensions["labdex"]["registry"].get("certificates")
    assert spec.capabilities == ["actions", "pages", "widgets"]
    job = run(admin, "certs", "check")
    assert job["status"] == "succeeded", admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
    assert job["result"]["checked"] >= 3 and job["result"]["expiring"] == 1  # + the inventory's https services (unreachable here)
    page = admin.get("/api/integrations/certs/pages/certificates").get_json()
    rows = {r["endpoint"]: r for r in page["sections"][0]["rows"]}
    soon = rows[f"127.0.0.1:{p1}"]
    assert soon["subject"] == "svc.lab.example.net" and soon["left"] in (4, 5) and soon["_tone"] == "err" and "self" in soon["trust"]
    assert rows[f"localhost:{p2}"]["_tone"] == "" and rows[f"localhost:{p2}"]["left"] >= 199
    assert any(r["endpoint"].endswith(":443") for r in rows.values())  # e.g. the firewall's web UI from the inventory
    n = db.session.scalars(db.select(Notification)).all()
    assert [x.event for x in n] == ["plugin.certificates.expiring"] and ("5 days" in n[0].title or "4 days" in n[0].title)
    # the same level isn't announced twice
    run(admin, "certs", "check")
    assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 1

    widgets.clear()
    w = next(x for x in admin.get("/api/widgets").get_json() if x["integration"] == "certs")["data"]
    assert w["items"][0]["label"] == f"127.0.0.1:{p1}" and w["items"][0]["tone"] == "err" and "need attention" in w["sub"]
