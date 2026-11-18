"""CPU, memory and disk from a host's agent; the 48 h health strip from the combined state."""
from datetime import timedelta

from labdex import health
from labdex.models import Agent, HostHealthEvent, db, utcnow

GB = 2 ** 30


def _agent(host, **metrics):
    a = Agent(id="ab12cd34", host=host, key_hash="x" * 64, connected="w1", last_seen=utcnow(), version="1.0",
              facts={"cpus": 8}, metrics={"cpu_percent": 23.0, "mem_total": 16 * GB, "mem_available": 4 * GB,
                                          "root_total": 100 * GB, "root_used": 58 * GB} | metrics)
    db.session.add(a)
    db.session.commit()
    return a


def test_usage_on_host_list_detail_and_sites(admin, app):
    with app.app_context():
        _agent("srv-pm-pve-01")
    h = admin.get("/api/hosts/srv-pm-pve-01").get_json()
    assert h["usage"]["cpu"] == 23.0 and h["usage"]["mem"] == 75.0 and h["usage"]["disk"] == 58.0 and h["usage"]["cpus"] == 8
    assert h["agent"] == {"linked": True, "version": "1.0", "mtls": False}
    rows = {x["name"]: x for x in admin.get("/api/hosts").get_json()}
    assert rows["srv-pm-pve-01"]["usage"]["mem"] == 75.0
    assert all(r["usage"] is None for n, r in rows.items() if n != "srv-pm-pve-01")
    assert "usage" not in admin.get("/api/hosts?fields=name").get_json()[0]
    home = next(s for s in admin.get("/api/dashboard").get_json()["sites"] if s["code"] == "home")
    assert home["usage"] == {"cpu": 23.0, "mem": 75.0, "disk": 58.0, "hosts": 1}


def test_no_usage_from_an_unlinked_agent(admin, app):
    with app.app_context():
        a = _agent("srv-pm-pve-01")
        a.last_seen = utcnow() - timedelta(hours=2)
        db.session.commit()
    assert admin.get("/api/hosts/srv-pm-pve-01").get_json()["usage"] is None


def test_strip_hours_and_reachable(app):
    with app.app_context():
        now = utcnow().replace(minute=0, second=0, microsecond=0)
        db.session.add_all([HostHealthEvent(host="h1", state="up", at=now - timedelta(hours=60)),
                            HostHealthEvent(host="h1", state="down", at=now - timedelta(hours=10)),
                            HostHealthEvent(host="h1", state="up", at=now - timedelta(hours=9))])
        db.session.commit()
        s = health.strip("h1", now=now)
        assert len(s["hours"]) == 48
        assert s["hours"][38] == "down" and s["hours"].count("down") == 1 and s["hours"].count("up") == 47
        assert s["up_pct"] == round(100 * 47 / 48, 1)
        # recorded only lately: the hours before are "none"
        db.session.add(HostHealthEvent(host="h2", state="up", at=now - timedelta(hours=2)))
        db.session.commit()
        s = health.strip("h2", now=now)
        assert s["hours"][:46] == ["none"] * 46 and s["hours"][46:] == ["up", "up"] and s["up_pct"] == 100.0
        assert health.strip("nobody", now=now) == {"hours": [], "up_pct": None}
