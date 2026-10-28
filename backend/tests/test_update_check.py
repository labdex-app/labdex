"""About › Updates: the release check's version comparison, its cache, and Check now."""
import pytest

from labdex.api import about_api


@pytest.mark.parametrize("tag, mine, newer", [
    ("v1.25.0", "1.24.1.post1", True),  # the release this instance missed
    ("v1.24.1", "1.24.1.post1", False),  # a post-release of the same version is not behind it
    ("v1.24.1", "1.24.1", False),
    ("v1.24.10", "1.24.9", True),
    ("v1.25.0", "1.25.0rc1", True),
    ("v1.25.0rc1", "1.24.1", True),
    ("v1.24.0", "1.25.0.dev0", False),
    ("release-2", "1.24.1", False),  # not a version: compared by its numbers
])
def test_newer(tag, mine, newer):
    assert about_api._newer(tag, mine) is newer


def test_check_now_skips_the_cache(admin, app, monkeypatch):
    calls = []

    class Answer:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"tag_name": calls[-1], "name": "", "html_url": "https://x/r", "published_at": ""}

    import requests
    tags = iter(["v1.24.1", "v1.25.0", "v1.26.0"])
    monkeypatch.setattr(requests, "get", lambda *a, **kw: calls.append(next(tags)) or Answer())
    about_api._release_cache.clear()
    app.config["LABDEX_VERSION"] = "1.24.1.post1"
    assert admin.post("/api/about/update-check").status_code == 409  # off in Settings
    admin.put("/api/admin/settings", json={"update_check": "true"})
    a = admin.get("/api/about").get_json()["update_check"]
    assert a["latest"]["tag"] == "v1.24.1" and a["newer"] is False and a["checked"]
    # a release published since: the About page keeps the cached answer, Check now asks again
    assert admin.get("/api/about").get_json()["update_check"]["latest"]["tag"] == "v1.24.1"
    monkeypatch.setattr(about_api, "FORCE_FLOOR", 0)
    u = admin.post("/api/about/update-check").get_json()
    assert u["latest"]["tag"] == "v1.25.0" and u["newer"] is True
    assert admin.get("/api/about").get_json()["update_check"]["newer"] is True  # the page sees it too
    # pressed twice in a row: the second answer comes from the cache
    monkeypatch.setattr(about_api, "FORCE_FLOOR", 30)
    assert admin.post("/api/about/update-check").get_json()["latest"]["tag"] == "v1.25.0" and len(calls) == 2
    # and the cache runs out after RELEASE_TTL
    url = about_api.DEFAULT_PROJECT
    t, rel = about_api._release_cache[url]
    about_api._release_cache[url] = (t - about_api.RELEASE_TTL - 1, rel)
    assert admin.get("/api/about").get_json()["update_check"]["latest"]["tag"] == "v1.26.0"
    about_api._release_cache.clear()
