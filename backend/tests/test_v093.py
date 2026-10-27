"""v0.9.3: plugins declare the labdex versions they work with."""
import shutil

from labdex import __version__
from labdex.plugins import compatibility, installer, trust

from .fakes import FakeServer
from .test_v04 import make_plugin, upload, zip_dir


def with_range(tmp_path, rng, version="1.0"):
    d = make_plugin(tmp_path, version)
    y = d / "plugin.yaml"
    y.write_text(y.read_text().replace("kind: module", f"kind: module\nlabdex: \"{rng}\""))
    return d


def test_compatibility():
    assert compatibility({}) == (True, "")
    assert compatibility({"labdex": ">=0.9"})[0]
    ok, why = compatibility({"labdex": ">=99"})
    assert not ok and why == f"needs labdex >=99, this is {__version__}"
    assert "isn't a version range" in compatibility({"labdex": "new"})[1]


def test_install_refuses_other_range(admin, tmp_path):
    r = upload(admin, zip_dir(with_range(tmp_path, ">=99")))
    assert r.status_code == 422 and "needs labdex >=99" in r.get_json()["error"]
    r = upload(admin, zip_dir(with_range(tmp_path, "soon", "1.1")))
    assert "isn't a version range" in r.get_json()["error"]
    info = upload(admin, zip_dir(with_range(tmp_path, ">=0.9,<2", "1.2"))).get_json()
    assert admin.post(f"/api/plugins/stage/{info['token']}/confirm", json={"accept_unsigned": True}).status_code == 201
    p = admin.get("/api/plugins/hello").get_json()
    assert p["requires"] == ">=0.9,<2" and not p["error"]


def test_not_loaded_when_out_of_range(app, tmp_path):
    from labdex.plugins import Registry
    base = tmp_path / "plugins"
    base.mkdir()
    shutil.copytree(with_range(tmp_path, "<0.1"), base / "hello")
    spec = Registry(str(base)).get("hello")
    assert spec.error.startswith("not loaded: needs labdex <0.1") and spec.module is None


def test_marketplace_shows_range(admin, tmp_path):
    from flask import Response
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    shutil.copytree(with_range(tmp_path, ">=99", "3.0"), src / "hello")
    pem, _ = trust.keygen()
    idx = installer.build_repository(src, out, pem, "Future repo")
    assert idx["plugins"][0]["labdex"] == ">=99"

    def handle(req):
        if req.path == "/index.json":
            return Response((out / "index.json").read_text(), mimetype="application/json")
        f = out / req.path.lstrip("/")
        return Response(f.read_bytes(), mimetype="application/zip") if f.is_file() else Response("", 404)
    srv = FakeServer(handle)
    try:
        url = srv.url + "/index.json"
        key = admin.post("/api/plugin-repos/preview", json={"url": url}).get_json()["key_id"]
        rid = admin.post("/api/plugin-repos", json={"url": url, "key_id": key}).get_json()["id"]
        p = next(x for x in admin.get("/api/marketplace").get_json()["plugins"] if x["id"] == "hello")
        assert p["requires"] == ">=99" and p["compatible"] is False and "needs labdex" in p["incompatible_reason"]
        r = admin.post("/api/marketplace/install", json={"repository_id": rid, "id": "hello"})
        assert r.status_code == 422 and "needs labdex" in r.get_json()["error"]
    finally:
        srv.close()
