"""Printable host and cable labels."""
import re
import xml.etree.ElementTree as ET

import segno

from labdex import labels


def parse(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.mimetype == "image/svg+xml"
    return ET.fromstring(resp.get_data(as_text=True))


def texts(root):
    return [t.text for t in root.iter("{http://www.w3.org/2000/svg}text")]


def test_qr_path_matches_the_code():
    n, d = labels.qr_path("https://labdex.example/hosts/abc")
    code = segno.make("https://labdex.example/hosts/abc", error="m", micro=False)
    assert n == len(code.matrix)
    dark = {(int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in re.finditer(r"M(\d+) (\d+)h(\d+)v1", d)}
    expect = {(x, y) for y, row in enumerate(code.matrix) for x, v in enumerate(row) if v}
    drawn = {(x + i, y) for x, y, ln in dark for i in range(ln)}
    assert drawn == expect


def test_host_label(admin):
    r = admin.get("/api/hosts/srv-vm-dns-01/label.svg")
    root = parse(r)
    t = texts(root)
    assert "srv-vm-dns-01" in t and "10.10.100.53" in t and "DC_TC" in t
    assert root.get("width") == "62mm" and root.get("height") == "29mm"
    assert parse(admin.get("/api/hosts/srv-vm-dns-01/label.svg?size=50x25")).get("width") == "50mm"
    assert parse(admin.get("/api/hosts/srv-vm-dns-01/label.svg?size=nonsense")).get("width") == "62mm"
    assert admin.get("/api/hosts/nope/label.svg").status_code == 404


def test_label_text_is_escaped():
    svg = labels.host_label({"name": "x<y>&z", "fun": "", "zone": "DC", "interfaces": []}, "https://x/y")
    ET.fromstring(svg)  # well-formed
    assert "&lt;y&gt;&amp;z" in svg


def test_cable_label(admin):
    a = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    r = admin.put("/api/hosts/srv-vm-dns-01", json={**a, "cables": [
        {"port": "eth0", "peer": "srv-vm-k3s-01", "peer_port": "eth9", "label": "C-0042", "length": "3", "medium": "copper", "speed": "1G"}]})
    assert r.status_code == 200, r.get_json()
    root = parse(admin.get("/api/cables/label.svg?host=srv-vm-dns-01&port=eth0"))
    t = texts(root)
    assert "C-0042" in t and "srv-vm-dns-01 eth0" in t and "srv-vm-k3s-01 eth9" in t and "copper 1G 3 m" in t
    assert admin.get("/api/cables/label.svg?host=srv-vm-dns-01&port=eth5").status_code == 404
    assert admin.get("/api/cables/label.svg?host=srv-vm-dns-01").status_code == 404


def test_hidden_hosts_have_no_label(admin, make_user):
    c = make_user("vi", role="viewer", scopes=["zone:CA_*"])
    assert c.get("/api/hosts/srv-vm-dns-01/label.svg").status_code == 404
