"""1.1: Traefik and Docker discovery; absolute service names."""

from .fakes import FakeDocker, FakeTraefik, docker_container
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


def plan_of(admin, job):
    return admin.get(f"/api/jobs/{job['id']}").get_json()["plan"]


def test_absolute_service_names(admin):
    h = host(admin, "srv-vm-mon-01")
    svcs = [*h["services"], {"name": "grafana-ext", "port": 3000, "scheme": "https", "dns_name": "grafana.example.com."},
            {"name": "grafana-in", "port": 3001, "scheme": "https", "dns_name": "graf.lab.example.net."}]
    h = put_host(admin, "srv-vm-mon-01", services=svcs)
    urls = {s["name"]: s for s in admin.get("/api/services").get_json() if s["host"] == "srv-vm-mon-01"}
    assert urls["grafana-ext"]["url"] == "https://grafana.example.com"
    recs = {(r["fqdn"], r["type"]) for r in h["dns"]}
    assert ("graf.lab.example.net", "CNAME") in recs  # inside the domain: a record
    assert not any(f.startswith("grafana.example.com") for f, _ in recs)  # outside: the link only
    r = admin.put("/api/hosts/srv-vm-mon-01", json={**h, "services": [*svcs[:-1], {"name": "x", "port": 1, "dns_name": "nodot."}]})
    assert r.status_code == 422


ROUTERS = [
    {"name": "grafana@docker", "rule": "Host(`grafana.example.com`)", "service": "grafana", "entryPoints": ["websecure"],
     "tls": {"certResolver": "le"}, "status": "enabled", "provider": "docker"},
    {"name": "ha@file", "rule": "Host(`ha.example.com`) && PathPrefix(`/`)", "service": "ha@file", "entryPoints": ["websecure"],
     "status": "enabled", "provider": "file"},
    {"name": "dashboard@internal", "rule": "PathPrefix(`/api`)", "service": "api@internal", "status": "enabled", "provider": "internal"},
    {"name": "old@docker", "rule": "Host(`old.example.com`)", "service": "old", "entryPoints": ["web"], "status": "disabled", "provider": "docker"},
]
SERVICES = [
    {"name": "grafana@docker", "loadBalancer": {"servers": [{"url": "http://172.18.0.5:3000"}]}, "provider": "docker"},
    {"name": "ha@file", "loadBalancer": {"servers": [{"url": "http://10.10.100.30:8123"}]}, "provider": "file"},
    {"name": "old@docker", "loadBalancer": {"servers": [{"url": "http://172.18.0.9:80"}]}, "provider": "docker"},
]


def test_traefik_import(admin):
    t = FakeTraefik(ROUTERS, SERVICES, auth=("ro", "pw"))
    try:
        make_instance(admin, "traefik", "proxy", {"url": t.url, "username": "ro", "password": "pw", "host": "dmz-vm-docker-01"})
        j = run(admin, "proxy", "test")
        assert "Traefik 3.4.0" in j["result"].get("message", ""), (j, admin.get(f"/api/jobs/{j['id']}").get_json()["log"])
        plan = run(admin, "proxy", "import_services")
        ch = {c["name"]: c for c in plan_of(admin, plan)}
        ha = ch["srv-vm-ha-01 ha"]["after"]  # backend matched to its host
        assert ha["port"] == 8123 and ha["scheme"] == "https" and ha["dns_name"] == "ha.example.com."
        gr = ch["dmz-vm-docker-01 grafana"]["after"]  # docker-internal backend: behind Traefik
        assert gr["port"] == 443 and gr["scheme"] == "https"
        assert ch["dmz-vm-docker-01 old"]["after"]["scheme"] == "http" and not any("dashboard" in n for n in ch)
        admin.post(f"/api/jobs/{plan['id']}/approve")
        s = {x["name"]: x for x in host(admin, "srv-vm-ha-01")["services"]}
        assert "ha" in s, (s, admin.get(f"/api/jobs/{plan['id']}").get_json()["log"])
        assert s["ha"]["dns_name"] == "ha.example.com."
        assert run(admin, "proxy", "import_services")["status"] == "succeeded"  # nothing left to do
        page = admin.get("/api/integrations/proxy/pages/routers").get_json()
        assert all(r["inventory"] == "yes" for r in page["sections"][0]["rows"])
    finally:
        t.srv.close()


def test_docker_import(admin):
    cs = [
        docker_container("grafana", "grafana/grafana:11", ports=[{"IP": "0.0.0.0", "PrivatePort": 3000, "PublicPort": 3000, "Type": "tcp"}],
                         labels={"com.docker.compose.service": "grafana", "com.docker.compose.project": "mon",
                                 "traefik.http.routers.grafana.rule": "Host(`grafana.example.com`)",
                                 "traefik.http.routers.grafana.entrypoints": "websecure",
                                 "traefik.http.services.grafana.loadbalancer.server.port": "3000"}),
        docker_container("pihole", "pihole/pihole", networks={"lan": {"IPAddress": "10.10.100.77", "MacAddress": "02:42:0a:0a:64:4d"}},
                         ports=[{"IP": "0.0.0.0", "PrivatePort": 80, "PublicPort": 8081, "Type": "tcp"}]),
        docker_container("cron", "alpine", state="exited"),
    ]
    d = FakeDocker(cs, token="tok")
    try:
        make_instance(admin, "docker", "dock", {"url": d.url, "token": "tok", "host": "dmz-vm-docker-01"})
        plan = run(admin, "dock", "import_containers")
        ch = plan_of(admin, plan)
        hosts = {c["after"]["fun_names"][0]: c["after"] for c in ch if c["kind"] == "host"}
        assert set(hosts) == {"grafana", "pihole"}  # stopped ones only when asked
        g, p = hosts["grafana"], hosts["pihole"]
        assert g["name"].startswith("srv-ct-grafana-") and g["runs_on"] == "dmz-vm-docker-01" and g["zone"] == "DMZ_TC"
        assert g["interfaces"] == [] and g["services"][0]["dns_name"] == "grafana.example.com." and "mon" in g["tags"]
        assert p["interfaces"][0]["ip4"] == "10.10.100.77" and p["services"][0]["port"] == 80  # its own address: the container port
        on_docker = [c for c in ch if c["kind"] == "service" and c["name"].startswith("dmz-vm-docker-01 ")]
        assert [c["after"]["port"] for c in on_docker] == [3000]  # grafana's published port lives on the Docker host
        done = admin.post(f"/api/jobs/{plan['id']}/approve").get_json()
        assert done["status"] == "applied", admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
        assert run(admin, "dock", "import_containers")["status"] == "succeeded"  # linked now: nothing new
        # status: a container stops
        d.containers[0]["State"] = "exited"
        admin.put("/api/integrations/dock", json={"settings": {"include_stopped": True}})
        res = run(admin, "dock", "poll")["result"]
        assert "1 down" in res["message"]
        page = admin.get("/api/integrations/dock/pages/containers").get_json()
        assert {r["name"] for r in page["sections"][0]["rows"]} == {"grafana", "pihole", "cron"}
    finally:
        d.srv.close()
