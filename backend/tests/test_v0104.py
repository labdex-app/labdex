"""0.10.4: the DNS overview; nmap progress in the job log."""
from .fakes import FakeTechnitium
from .test_plugins import make_instance


def test_dns_overview(admin, make_user):
    d = admin.get("/api/dns").get_json()
    assert d["servers"] == [] and any(r["name"] == "srv-vm-dns-01.srv.lab.example.net" and r["type"] == "A" for r in d["generated"])
    t = FakeTechnitium("lab.example.net")
    try:
        t.records.append({"name": "srv-vm-dns-01.srv.lab.example.net", "type": "A", "ttl": 300, "rData": {"ipAddress": "10.10.100.53"}, "comments": "labdex"})
        t.records.append({"name": "gone.lab.example.net", "type": "A", "ttl": 300, "rData": {"ipAddress": "10.10.100.9"}, "comments": "labdex"})
        make_instance(admin, "technitium", "dns", {"url": t.url, "token": t.TOKEN})
        d = admin.get("/api/dns").get_json()
        s = d["servers"][0]
        assert s["integration"] == "dns" and not s.get("error")
        st = {(r["name"], r["type"]): r["status"] for r in s["records"]}
        assert st[("srv-vm-dns-01.srv.lab.example.net", "A")] == "in sync"
        assert st[("gone.lab.example.net", "A")] == "stale"
        assert st[("manual.lab.example.net", "A")] == "unmanaged"
        assert "missing" in st.values()
        # a viewer limited to campus sees no datacenter host records
        v = make_user("v", role="viewer", scopes=["zone:CA_*"])
        d = v.get("/api/dns").get_json()
        assert not any(r.get("host") == "srv-vm-dns-01" for r in d["generated"])
    finally:
        t.srv.close()
