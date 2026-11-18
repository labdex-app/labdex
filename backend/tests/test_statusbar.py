"""The status bar: git sync per site, linked agents, vault state."""
from labdex.models import Site, db


def test_statusbar_without_git(admin):
    d = admin.get("/api/statusbar").get_json()
    assert d["sites"] == [{"code": "home", "git": None}]
    assert d["agents"] == {"linked": 0, "total": 0}
    assert set(d["vault"]) == {"initialized", "sealed"}


def test_statusbar_shows_short_commit(admin):
    s = db.session.get(Site, "home")
    s.git = {"url": "https://git.example.com/lab.git", "branch": "main"}
    s.git_state = {"commit": "a41c9e2f00d1b2c3", "synced_at": "2026-11-18T10:00:00Z", "error": None}
    db.session.commit()
    git = admin.get("/api/statusbar").get_json()["sites"][0]["git"]
    assert git == {"commit": "a41c9e2", "synced_at": "2026-11-18T10:00:00Z", "error": False}


def test_statusbar_needs_sign_in(loaded):
    from tests.conftest import client
    assert client(loaded).get("/api/statusbar").status_code == 401
