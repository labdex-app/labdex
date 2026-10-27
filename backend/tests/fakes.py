"""Tiny fake OPNsense and Technitium APIs, served on localhost for the plugin tests."""
import base64
import logging
import json
import threading
import uuid
from urllib.parse import parse_qs

from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response


logging.getLogger("werkzeug").setLevel(logging.ERROR)


class FakeServer:
    def __init__(self, handler):
        self.server = make_server("127.0.0.1", 0, Request.application(handler))
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class FakeOPNsense:
    KEY, SECRET = "k", "s"

    def __init__(self):
        self.aliases = {}  # uuid -> dict
        self.categories = {}  # uuid -> {name}
        self.reconfigured = 0
        self.rules = []
        self.nat = []
        self.subnets = [{"uuid": "sub-main", "subnet": "10.10.10.0/24"}, {"uuid": "sub-srv", "subnet": "10.10.100.0/24"}]
        self.reservations = {}
        self.leases = [{"address": "10.10.10.88", "hwaddr": "aa:bb:cc:00:00:88", "hostname": "tablet.", "if_descr": "LAN"}]
        self.kea_reconfigured = 0
        self.search_methods = []
        self.ndp = [{"ip": "fe80::1%igc1", "mac": "aa:bb:cc:00:00:01", "intf": "igc1", "manufacturer": ""},
                    {"ip": "fd00:10:10:10::77", "mac": "aa:bb:cc:00:00:77", "intf": "igc1", "manufacturer": "Acme"}]
        self.leases6 = [{"address": "fd00:10:10:10::1234", "prefix_len": 128, "type": "IA_NA", "hwaddr": "aa:bb:cc:00:12:34", "hostname": "laptop"},
                        {"address": "2001:db8:ab00:f0::", "prefix_len": 60, "type": "IA_PD", "hwaddr": "", "hostname": ""}]
        # interfaces/overview/interfaces_info rows (only what labdex reads)
        self.interfaces = [{"identifier": "wan", "description": "WAN", "device": "igc0", "ifctl.prefix": ["2001:db8:ab00::/56"],
                            "ipv4": [{"ipaddr": "192.0.2.10/24"}]},
                           {"identifier": "lan", "description": "LAN", "device": "igc1", "ipv4": [{"ipaddr": "10.10.10.254/24"}]},
                           {"identifier": "opt1", "description": "SRV", "device": "vlan0.100", "ipv4": [{"ipaddr": "10.10.100.254/24"}], "vlan_tag": 100},
                           {"identifier": "opt2", "description": "MGMT", "device": "vlan0.3", "vlan_tag": 3}]
        self.arp = [{"ip": "10.10.10.77", "mac": "AA:BB:CC:00:00:77", "hostname": "phone", "manufacturer": "Acme"}]
        self.srv = FakeServer(self.handle)
        self.url = self.srv.url

    def add(self, name, content, description="", type_="host"):
        u = str(uuid.uuid4())
        self.aliases[u] = {"enabled": "1", "name": name, "type": type_, "content": content, "description": description}
        return u

    def by_name(self, name):
        return next((a for a in self.aliases.values() if a["name"] == name), None)

    def handle(self, req: Request):
        auth = req.headers.get("Authorization", "")
        if auth != "Basic " + base64.b64encode(f"{self.KEY}:{self.SECRET}".encode()).decode():
            return Response("{}", 401)
        p = req.path
        if "search" in p:
            # Grid searches: OPNsense reads rowCount/current from the JSON body (26.1 UIModelGrid).
            self.search_methods.append((p, req.method, (req.get_json(silent=True) or {}).get("rowCount")))
        if p == "/api/firewall/alias/export":
            return _json({"aliases": {"alias": self.aliases}})
        if p == "/api/firewall/alias/addItem":
            a = req.get_json()["alias"]
            if self.by_name(a["name"]):
                return _json({"result": "failed", "validations": {"alias.name": "exists"}})
            u = self.add(a["name"], a["content"], a["description"], a["type"])
            self.aliases[u]["categories"] = a.get("categories", "")
            return _json({"result": "saved", "uuid": u})
        if p.startswith("/api/firewall/alias/setItem/"):
            u = p.rsplit("/", 1)[1]
            self.aliases[u].update(req.get_json()["alias"])
            return _json({"result": "saved"})
        if p.startswith("/api/firewall/alias/delItem/"):
            self.aliases.pop(p.rsplit("/", 1)[1], None)
            return _json({"result": "deleted"})
        if p == "/api/firewall/alias/reconfigure":
            self.reconfigured += 1
            return _json({"status": "ok"})
        if p == "/api/firewall/category/search_item":
            return _json({"rows": [{"uuid": u, **c} for u, c in self.categories.items()]})
        if p == "/api/firewall/category/add_item":
            u = str(uuid.uuid4())
            self.categories[u] = {"name": req.get_json()["category"]["name"]}
            return _json({"result": "saved", "uuid": u})
        if p == "/api/kea/leases4/search":
            return _json({"rows": self.leases})
        if p == "/api/kea/dhcpv4/search_subnet":
            return _json({"rows": self.subnets})
        if p == "/api/kea/dhcpv4/search_reservation":
            return _json({"rows": [{"uuid": u, **r, "subnet": next(s["subnet"] for s in self.subnets if s["uuid"] == r["subnet"])}
                                   for u, r in self.reservations.items()]})
        if p == "/api/kea/dhcpv4/add_reservation":
            u = str(uuid.uuid4())
            self.reservations[u] = dict(req.get_json()["reservation"])
            return _json({"result": "saved", "uuid": u})
        if p.startswith("/api/kea/dhcpv4/set_reservation/"):
            self.reservations[p.rsplit("/", 1)[1]].update(req.get_json()["reservation"])
            return _json({"result": "saved"})
        if p.startswith("/api/kea/dhcpv4/del_reservation/"):
            self.reservations.pop(p.rsplit("/", 1)[1], None)
            return _json({"result": "deleted"})
        if p == "/api/kea/service/reconfigure":
            self.kea_reconfigured += 1
            return _json({"status": "ok"})
        if p == "/api/firewall/filter/search_rule":
            return _json({"rows": self.rules, "rowCount": len(self.rules), "total": len(self.rules), "current": 1})
        if p == "/api/firewall/filter/add_rule":
            r = dict(req.get_json()["rule"])
            r["uuid"] = str(uuid.uuid4())
            self.rules.append(r)
            return _json({"result": "saved", "uuid": r["uuid"]})
        if p.startswith("/api/firewall/filter/set_rule/"):
            u = p.rsplit("/", 1)[1]
            next(r for r in self.rules if r.get("uuid") == u).update(req.get_json()["rule"])
            return _json({"result": "saved"})
        if p.startswith("/api/firewall/filter/del_rule/"):
            u = p.rsplit("/", 1)[1]
            self.rules = [r for r in self.rules if r.get("uuid") != u]
            return _json({"result": "deleted"})
        if p == "/api/firewall/d_nat/search_rule":
            return _json({"rows": self.nat, "rowCount": len(self.nat), "total": len(self.nat), "current": 1})
        if p == "/api/firewall/d_nat/add_rule":
            r = dict(req.get_json()["rule"])
            r["uuid"] = str(uuid.uuid4())
            self.nat.append(r)
            return _json({"result": "saved", "uuid": r["uuid"]})
        if p.startswith("/api/firewall/d_nat/set_rule/"):
            u = p.rsplit("/", 1)[1]
            next(r for r in self.nat if r.get("uuid") == u).update(req.get_json()["rule"])
            return _json({"result": "saved"})
        if p.startswith("/api/firewall/d_nat/del_rule/"):
            u = p.rsplit("/", 1)[1]
            self.nat = [r for r in self.nat if r.get("uuid") != u]
            return _json({"result": "deleted"})
        if p == "/api/firewall/d_nat/apply":
            self.nat_applied = getattr(self, "nat_applied", 0) + 1
            return _json({"status": "OK"})
        if p == "/api/firewall/filter/apply":
            self.filter_applied = getattr(self, "filter_applied", 0) + 1
            return _json({"status": "OK"})
        if p == "/api/interfaces/overview/interfaces_info":
            return _json({"rows": self.interfaces, "rowCount": len(self.interfaces), "total": len(self.interfaces), "current": 1})
        if p == "/api/diagnostics/interface/getNdp":
            return _json(self.ndp)
        if p == "/api/kea/leases6/search":
            return _json({"rows": self.leases6})
        if p == "/api/diagnostics/interface/getArp":

            return _json(self.arp)
        return Response("not found", 404)


class FakeTechnitium:
    TOKEN = "t0ken"

    def _zone_of(self, name):
        return next((z for z in sorted(self.zones, key=len, reverse=True) if name == z or name.endswith("." + z)), None)

    def __init__(self, zone, require_header=False, zones=None, can_list=True):
        self.zone = zone
        self.zones = list(zones or [zone])  # primary zones on the server
        self.can_list = can_list  # False: the token lacks "Zones: View"
        self.zone_types = {}  # zone → "Forwarder", "Secondary", … (default Primary)
        self.require_header = require_header  # 15.x: "Authorization: Bearer <token>"; the form field is legacy
        self.records = [{"name": f"manual.{zone}", "type": "A", "ttl": 300, "rData": {"ipAddress": "10.10.100.99"}, "comments": ""}]
        self.srv = FakeServer(self.handle)
        self.url = self.srv.url

    def handle(self, req: Request):
        form = {k: v[0] for k, v in parse_qs(req.get_data(as_text=True)).items()}
        auth = req.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else (None if self.require_header else form.get("token"))
        if token != self.TOKEN:
            return _json({"status": "invalid-token", "errorMessage": "bad token"})
        p = req.path
        if p == "/api/zones/list":
            if not self.can_list:
                return _json({"status": "error", "errorMessage": "Access was denied."})
            return _json({"status": "ok", "response": {"zones": [{"name": z, "type": self.zone_types.get(z, "Primary"), "disabled": False} for z in self.zones]
                                                       + [{"name": "0.in-addr.arpa", "type": "Primary", "internal": True}]}})
        if p == "/api/zones/create":
            self.zones.append(form["zone"])
            self.created_zones = getattr(self, "created_zones", []) + [form["zone"]]
            return _json({"status": "ok", "response": {"domain": form["zone"]}})
        if p == "/api/zones/records/get":
            z = form.get("zone") or form.get("domain")
            if z not in self.zones:
                return _json({"status": "error", "errorMessage": f"No such zone was found: {z}"})
            return _json({"status": "ok", "response": {"records": [r for r in self.records if self._zone_of(r["name"]) == z]}})
        if p in ("/api/zones/records/add", "/api/zones/records/delete"):
            t = form["type"]
            if form.get("zone") not in self.zones or self._zone_of(form["domain"]) != form.get("zone"):
                return _json({"status": "error", "errorMessage": f"{form['domain']} isn't in zone {form.get('zone')}"})
            rdata = {"A": lambda: {"ipAddress": form["ipAddress"]}, "AAAA": lambda: {"ipAddress": form["ipAddress"]},
                     "CNAME": lambda: {"cname": form["cname"]}, "TXT": lambda: {"text": form["text"]},
                     "MX": lambda: {"preference": int(form["preference"]), "exchange": form["exchange"]},
                     "SRV": lambda: {k: (int(form[k]) if k != "target" else form[k]) for k in ("priority", "weight", "port", "target")}}[t]()
            rec = {"name": form["domain"], "type": t, "ttl": int(form.get("ttl", 300)), "rData": rdata, "comments": form.get("comments", "")}
            if p.endswith("add"):
                self.records.append(rec)
            else:
                self.records = [r for r in self.records if not (r["name"] == rec["name"] and r["type"] == t and r["rData"] == rdata)]
            return _json({"status": "ok", "response": {}})
        return Response("not found", 404)


def _json(data):
    return Response(json.dumps(data), mimetype="application/json")


class FakeS3:
    """Path-style S3 with SigV4 header checks (not signature verification)."""

    def __init__(self, bucket="bk"):
        self.bucket, self.objects, self.auth = bucket, {}, []
        self.srv = FakeServer(self.handle)
        self.url = self.srv.url

    def handle(self, req: Request):
        a = req.headers.get("Authorization", "")
        self.auth.append(a)
        if not a.startswith("AWS4-HMAC-SHA256 Credential=AK/") or "Signature=" not in a or not req.headers.get("x-amz-date"):
            return Response("denied", 403)
        parts = req.path.lstrip("/").split("/", 1)
        if parts[0] != self.bucket:
            return Response("no bucket", 404)
        key = parts[1] if len(parts) > 1 else ""
        if req.method == "PUT":
            self.objects[key] = req.get_data()
            return Response("", 200)
        if req.method == "DELETE":
            self.objects.pop(key, None)
            return Response("", 204)
        if req.method == "GET" and not key:
            prefix = req.args.get("prefix", "")
            items = "".join(f"<Contents><Key>{k}</Key><Size>{len(v)}</Size><LastModified>2026-10-23T10:00:00Z</LastModified></Contents>"
                            for k, v in sorted(self.objects.items()) if k.startswith(prefix))
            return Response(f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{items}</ListBucketResult>',
                            mimetype="application/xml")
        return Response("nope", 400)


class FakePrometheus:
    """The Prometheus query API: /api/v1/query and /api/v1/query_range."""

    def __init__(self, token=""):
        self.token = token
        self.up = {}  # (instance, job) -> 0/1
        self.cpu = {}  # instance -> [values]
        self.queries = []

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json
        import time as _t
        if self.token and req.headers.get("Authorization") != f"Bearer {self.token}":
            return Response("unauthorized", 401)
        q = req.args.get("query", "")
        self.queries.append(q)
        if req.path == "/api/v1/query":
            res = [{"metric": {"__name__": "up", "instance": i, "job": j}, "value": [_t.time(), str(v)]} for (i, j), v in self.up.items()]
            return Response(_json.dumps({"status": "success", "data": {"resultType": "vector", "result": res}}), mimetype="application/json")
        if req.path == "/api/v1/query_range":
            if "node_cpu" not in q:
                return Response(_json.dumps({"status": "success", "data": {"resultType": "matrix", "result": []}}), mimetype="application/json")
            res = [{"metric": {"instance": i}, "values": [[_t.time() - 300 * (len(v) - n), str(x)] for n, x in enumerate(v)]} for i, v in self.cpu.items()]
            return Response(_json.dumps({"status": "success", "data": {"resultType": "matrix", "result": res}}), mimetype="application/json")
        return Response("not found", 404)


class FakeBeszel:
    """Beszel's hub API (PocketBase): password auth and the systems collection."""

    def __init__(self, email="me@lab", password="pw"):
        self.email, self.password = email, password
        self.systems = []
        self.created = []

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json
        js = lambda d, s=200: Response(_json.dumps(d), s, mimetype="application/json")
        if req.path == "/api/collections/users/auth-with-password":
            b = req.get_json(silent=True) or {}
            if b.get("identity") != self.email or b.get("password") != self.password:
                return js({"message": "Failed to authenticate."}, 400)
            return js({"token": "pbtoken", "record": {"id": "u1", "email": self.email}})
        if req.headers.get("Authorization") != "pbtoken":
            return js({"message": "unauthorized"}, 401)
        if req.path == "/api/collections/systems/records" and req.method == "GET":
            page, per = int(req.args.get("page", 1)), int(req.args.get("perPage", 30))
            items = self.systems[(page - 1) * per: page * per]
            return js({"page": page, "perPage": per, "totalItems": len(self.systems),
                       "totalPages": max(1, -(-len(self.systems) // per)), "items": items})
        if req.path == "/api/collections/systems/records" and req.method == "POST":
            b = req.get_json()
            rec = {"id": f"s{len(self.systems) + 1}", "status": "pending", "info": {}, **b}
            self.systems.append(rec)
            self.created.append(b)
            return js(rec)
        return js({"message": "not found"}, 404)


class FakeKuma:
    """Uptime Kuma's socket.io API: login, monitorList, heartbeatList, add/editMonitor/deleteMonitor."""

    def __init__(self, username="admin", password="pw"):
        import socketio
        from werkzeug.serving import make_server
        self.username, self.password = username, password
        self.monitors: dict[int, dict] = {}
        self.status: dict[int, int] = {}
        self.calls = []
        self.sio = sio = socketio.Server(async_mode="threading")
        self.authed = set()

        def push(sid):
            sio.emit("monitorList", {str(k): v for k, v in self.monitors.items()}, to=sid)
            for mid, st in self.status.items():
                sio.emit("heartbeatList", (mid, [{"monitorID": mid, "status": st, "time": "2026-10-24 10:00:00"}], True), to=sid)

        @sio.event
        def login(sid, data):
            self.calls.append(("login", data.get("username")))
            if data.get("username") != self.username or data.get("password") != self.password:
                return {"ok": False, "msg": "Incorrect username or password."}
            self.authed.add(sid)
            push(sid)
            return {"ok": True, "token": "jwt"}

        @sio.event
        def add(sid, data):
            if sid not in self.authed:
                return {"ok": False, "msg": "You are not logged in."}
            mid = max(self.monitors, default=0) + 1
            self.monitors[mid] = {**data, "id": mid, "active": True}
            self.calls.append(("add", data))
            push(sid)
            return {"ok": True, "msg": "Added Successfully.", "monitorID": mid}

        @sio.event
        def editMonitor(sid, data):
            self.monitors[int(data["id"])] = data
            self.calls.append(("edit", data))
            push(sid)
            return {"ok": True, "msg": "Saved.", "monitorID": data["id"]}

        @sio.event
        def deleteMonitor(sid, mid):
            self.monitors.pop(int(mid), None)
            self.calls.append(("delete", mid))
            push(sid)
            return {"ok": True, "msg": "Deleted Successfully."}

        self.server = make_server("127.0.0.1", 0, socketio.WSGIApp(sio), threaded=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class FakeProxmox:
    """The parts of the Proxmox VE API labdex reads."""

    TOKEN = "PVEAPIToken=labdex@pve!inv=s3cret"

    def __init__(self):
        self.nodes = ["pve1", "pve2"]
        self.guests = []  # {vmid, node, type, name, status, config: {...}, agent: [...] | None, lxc_ifaces: [...]}
        self.calls = []  # (method, path, form) for every write
        self.next_id = 300
        self.fail = ""  # a path fragment whose task ends with an error

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json
        js = lambda d, s=200: Response(_json.dumps({"data": d}), s, mimetype="application/json")
        if req.headers.get("Authorization") != self.TOKEN:
            return Response("authentication failure", 401)
        p = req.path.removeprefix("/api2/json/")
        if req.method in ("POST", "PUT"):
            return self.write(req, p, js)
        if p == "cluster/nextid":
            return js(str(self.next_id))
        if p.startswith("nodes/") and "/tasks/" in p:
            upid = p.split("/tasks/")[1].removesuffix("/status")
            from urllib.parse import unquote
            bad = self.fail and self.fail in unquote(upid)
            return js({"status": "stopped", "exitstatus": "boom" if bad else "OK"})
        if p == "nodes":
            return js([{"node": n, "status": "online"} for n in self.nodes])
        if p == "cluster/resources":
            return js([{"id": f"{g['type']}/{g['vmid']}", "vmid": g["vmid"], "node": g["node"], "type": g["type"], "name": g["name"],
                        "status": g["status"], "template": g.get("template", 0), "tags": g.get("tags", "")} for g in self.guests])
        parts = p.split("/")
        if len(parts) >= 5 and parts[0] == "nodes":
            g = next((x for x in self.guests if str(x["vmid"]) == parts[3] and x["node"] == parts[1]), None)
            if g is None:
                return js(None, 500)
            if parts[4] == "config":
                return js(g.get("config", {}))
            if parts[4:] == ["agent", "network-get-interfaces"]:
                return js({"result": g["agent"]}) if g.get("agent") is not None else js(None, 500)
            if parts[4] == "interfaces":
                return js(g.get("lxc_ifaces", []))
        return js(None, 404)


    def write(self, req, p, js):
        form = dict(req.form)
        self.calls.append((req.method, p, form))
        parts = p.split("/")
        upid = lambda node, what: f"UPID:{node}:0000:{what}:{p}:root@pam:"
        if len(parts) == 3 and parts[2] == "lxc":  # nodes/<node>/lxc: create a container
            vmid = int(form["vmid"])
            self.guests.append({"vmid": vmid, "node": parts[1], "type": "lxc", "name": form["hostname"], "status": "stopped",
                                "config": {k: v for k, v in form.items() if k.startswith("net")}})
            self.next_id = max(self.next_id, vmid + 1)
            return js(upid(parts[1], "vzcreate"))
        if len(parts) == 5 and parts[4] == "clone":  # nodes/<node>/qemu/<tpl>/clone
            vmid, node = int(form["newid"]), form.get("target", parts[1])
            self.guests.append({"vmid": vmid, "node": node, "type": "qemu", "name": form["name"], "status": "stopped",
                                "config": {"scsi0": f"local-lvm:vm-{vmid}-disk-0,size=8G"}})
            self.next_id = max(self.next_id, vmid + 1)
            return js(upid(parts[1], "qmclone"))
        g = next((x for x in self.guests if len(parts) > 3 and str(x["vmid"]) == parts[3] and x["node"] == parts[1]), None)
        if g is None:
            return js(None, 500)
        if parts[4] == "config":
            g["config"].update(form)
            return js(None)
        if parts[4] == "resize":
            g["config"][form["disk"]] = f"local-lvm:vm-{g['vmid']}-disk-0,size={form['size']}"
            return js(None)
        if parts[4:] == ["status", "start"]:
            g["status"] = "running"
            return js(upid(parts[1], "start"))
        return js(None, 404)


class FakeKube:
    """Kubernetes API: /version, nodes, pods."""

    def __init__(self, token="kt"):
        self.token = token
        self.nodes = []
        self.pods = []

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json
        js = lambda d, s=200: Response(_json.dumps(d), s, mimetype="application/json")
        if req.headers.get("Authorization") != f"Bearer {self.token}":
            return js({"kind": "Status", "code": 401}, 401)
        if req.path == "/version":
            return js({"gitVersion": "v1.31.2+k3s1"})
        if req.path == "/api/v1/nodes":
            return js({"items": self.nodes})
        if req.path == "/api/v1/pods":
            return js({"items": self.pods})
        return js({}, 404)

    @staticmethod
    def node(name, ip, ready=True, roles=("control-plane", "master"), version="v1.31.2+k3s1"):
        return {"metadata": {"name": name, "labels": {f"node-role.kubernetes.io/{r}": "true" for r in roles}},
                "spec": {}, "status": {"addresses": [{"type": "InternalIP", "address": ip}, {"type": "Hostname", "address": name}],
                                       "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                                       "nodeInfo": {"kubeletVersion": version, "osImage": "Debian GNU/Linux 12"}}}


class FakeTailnet:
    """Tailscale's /api/v2 devices and Headscale's /api/v1/node."""

    def __init__(self, key="tskey"):
        self.key = key
        self.devices = []

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json
        js = lambda d, s=200: Response(_json.dumps(d), s, mimetype="application/json")
        if req.headers.get("Authorization") != f"Bearer {self.key}":
            return js({"message": "unauthorized"}, 401)
        if req.path == "/api/v2/tailnet/-/devices":
            return js({"devices": self.devices})
        if req.path == "/api/v1/node":
            return js({"nodes": [{"name": d["hostname"], "givenName": d["name"].split(".")[0], "ipAddresses": d["addresses"],
                                  "online": d.get("connectedToControl", False), "lastSeen": d.get("lastSeen", ""),
                                  "user": {"name": "mike"}} for d in self.devices]})
        return js({}, 404)


class FakeUniFi:
    """UniFi Network integration API (v1): sites, clients, devices, paged."""

    def __init__(self, key="uk"):
        self.key = key
        self.sites = [{"id": "s-1", "internalReference": "default", "name": "Default"}]
        self.clients = []
        self.devices = []

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json
        if self.key is not None and req.headers.get("X-API-KEY") != self.key:
            return Response(_json.dumps({"message": "Unauthorized"}), 401, mimetype="application/json")
        p = req.path.removeprefix("/proxy/network/integration/v1/")
        offset, limit = int(req.args.get("offset", 0)), int(req.args.get("limit", 25))
        data = {"sites": self.sites, "sites/s-1/clients": self.clients, "sites/s-1/devices": self.devices}.get(p)
        if data is None:
            return Response("{}", 404, mimetype="application/json")
        page = data[offset:offset + limit]
        return Response(_json.dumps({"offset": offset, "limit": limit, "count": len(page), "totalCount": len(data), "data": page}),
                        mimetype="application/json")


class FakeUniFiOS:
    """A UniFi OS console / UniFi OS Server (classic=False) or the Network application on its own
    (classic=True): a cookie login, then the Network application's own API."""

    def __init__(self, classic=False, user="viewer", password="pw", mfa_user="owner"):
        self.classic, self.user, self.password, self.mfa_user = classic, user, password, mfa_user
        self.sessions = set()
        self.logged_out = 0
        self.sites = [{"_id": "5f0", "name": "default", "desc": "Default"}, {"_id": "5f1", "name": "lab2", "desc": "Garage"}]
        self.clients = {"default": [{"mac": "AA:BB:CC:00:00:01", "ip": "172.16.0.5", "hostname": "laptop"},
                                    {"mac": "aa:bb:cc:00:00:02", "ip": "172.16.0.6", "name": "tv", "hostname": "android-123"},
                                    {"mac": "aa:bb:cc:00:00:03", "hostname": "no-ip"}],
                        "lab2": [{"mac": "aa:bb:cc:00:00:04", "ip": "172.16.9.9", "hostname": "garage-cam"}]}
        self.devices = {"default": [{"name": "core", "model": "USW-Pro-24", "mac": "aa:aa:aa:aa:aa:01", "ip": "10.10.3.2", "state": 1},
                                    {"name": "ap-attic", "model": "U7-Pro", "mac": "aa:aa:aa:aa:aa:02", "ip": "10.10.3.40", "state": 0}],
                        "lab2": []}

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json
        login, logout, prefix, cookie = (("/api/login", "/api/logout", "", "unifises") if self.classic
                                         else ("/api/auth/login", "/api/auth/logout", "/proxy/network", "TOKEN"))

        def j(data, status=200, **kw):
            return Response(_json.dumps(data), status, mimetype="application/json", **kw)

        if req.path == login and req.method == "POST":
            b = req.get_json(silent=True) or {}
            if b.get("username") == self.mfa_user and b.get("password") == self.password:
                return j({"code": "MFA_AUTH_REQUIRED", "message": "api.err.Ubic2faTokenRequired"}, 499)
            if (b.get("username"), b.get("password")) != (self.user, self.password):
                return j({"meta": {"rc": "error", "msg": "api.err.Invalid"}, "data": []}, 400 if self.classic else 401)
            tok = uuid.uuid4().hex
            self.sessions.add(tok)
            r = j({"meta": {"rc": "ok"}, "data": []} if self.classic else {"username": self.user})
            r.set_cookie(cookie, tok)
            if self.classic:
                r.set_cookie("csrf_token", "csrf-" + tok)
            else:
                r.headers["X-CSRF-Token"] = "csrf-" + tok
            return r
        tok = req.cookies.get(cookie)
        if tok not in self.sessions:
            return j({"meta": {"rc": "error", "msg": "api.err.LoginRequired"}, "data": []}, 401)
        if req.path == logout and req.method == "POST":
            if req.headers.get("X-CSRF-Token") != "csrf-" + tok:
                return j({"meta": {"rc": "error", "msg": "csrf"}}, 403)
            self.sessions.discard(tok)
            self.logged_out += 1
            return j({"meta": {"rc": "ok"}, "data": []})
        p = req.path.removeprefix(prefix)
        if p == "/api/self/sites":
            return j({"meta": {"rc": "ok"}, "data": self.sites})
        parts = p.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["api", "s"] and parts[3] == "stat":
            site, what = parts[2], parts[4]
            data = {"sta": self.clients, "device": self.devices}.get(what, {}).get(site)
            if data is None:
                return j({"meta": {"rc": "error", "msg": "api.err.NoSiteContext"}, "data": []}, 400)
            return j({"meta": {"rc": "ok"}, "data": data})
        return j({}, 404)


class FakeSiteManager:
    """api.ui.com: consoles, devices, and the connector to each console's integration API."""

    def __init__(self, key="cloudkey"):
        self.key = key
        self.consoles = {"c-home": FakeUniFi(key=None), "c-shed": None}  # c-shed: the connector can't reach it
        self.names = {"c-home": "Home UDM", "c-shed": "Shed UCG"}
        self.devices = {"c-shed": [{"name": "shed-sw", "model": "USW-Lite-8", "shortname": "USL8", "mac": "AA:AA:AA:AA:AA:09",
                                    "ip": "10.10.3.9", "status": "online"}],
                        "c-home": [{"name": "core", "model": "USW-Pro-24", "mac": "aa:aa:aa:aa:aa:01", "ip": "10.10.3.2", "status": "online"}]}

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json

        def j(data, status=200):
            return Response(_json.dumps(data), status, mimetype="application/json")

        if req.headers.get("X-API-KEY") != self.key:
            return j({"code": "unauthorized", "message": "unauthorized"}, 401)
        if req.path == "/v1/hosts":
            ids = sorted(self.consoles)
            # one per page, to exercise nextToken
            i = int(req.args.get("nextToken") or 0)
            host = {"id": ids[i], "type": "console", "reportedState": {"name": self.names[ids[i]], "hostname": ids[i]}}
            return j({"data": [host], "httpStatusCode": 200, **({"nextToken": str(i + 1)} if i + 1 < len(ids) else {})})
        if req.path == "/v1/devices":
            return j({"data": [{"hostId": h, "hostName": self.names[h], "devices": d} for h, d in self.devices.items()]})
        pre = "/v1/connector/consoles/"
        if req.path.startswith(pre):
            cid, _, rest = req.path[len(pre):].partition("/")
            console = self.consoles.get(cid)
            if console is None:
                return j({"code": "not_found", "message": "console not reachable"}, 404)
            environ = dict(req.environ, PATH_INFO="/" + rest)
            from werkzeug.wrappers import Request as _Req
            return console(_Req(environ))
        return j({}, 404)


class FakeRouter:
    """OPNsense FRR diagnostics and Cisco IOS-XE RESTCONF, enough for routing discovery."""

    def __init__(self, user="k", password="s"):
        self.user, self.password = user, password
        self.peers = {"10.10.3.254": {"remoteAs": 65001, "state": "Established", "pfxRcd": 3, "peerUptime": "2d", "desc": "oob router"},
                      "192.0.2.1": {"remoteAs": 64512, "state": "Active", "pfxRcd": 0, "peerUptime": "never"}}
        self.routes4 = [
            {"prefix": "0.0.0.0/0", "protocol": "static", "selected": True, "ip": "192.0.2.1", "interfaceName": "igc0"},
            {"prefix": "10.10.100.0/24", "protocol": "connected", "selected": True, "interfaceName": "vlan100"},
            {"prefix": "10.10.10.0/24", "protocol": "connected", "selected": True, "interfaceName": "vlan10"},
            {"prefix": "10.20.0.0/16", "protocol": "bgp", "selected": True, "ip": "10.10.3.254", "interfaceName": "vlan3"},
            {"prefix": "10.10.100.53/32", "protocol": "ospf", "selected": True, "ip": "10.10.100.1", "interfaceName": "vlan100"},
            {"prefix": "172.31.0.0/24", "protocol": "static", "selected": False, "ip": "10.10.3.1"},
        ]

    def __call__(self, req):
        from werkzeug.wrappers import Response
        import json as _json
        js = lambda d, s=200: Response(_json.dumps(d), s, mimetype="application/json")
        a = req.authorization
        if not a or a.username != self.user or a.password != self.password:
            return Response("", 401)
        if req.path == "/api/quagga/diagnostics/bgpsummary":
            return js({"response": {"ipv4Unicast": {"routerId": "10.10.3.253", "as": 65000, "peers": self.peers}}})
        if req.path == "/api/quagga/diagnostics/search_generalroute4":
            return js({"rows": self.routes4, "total": len(self.routes4), "rowCount": len(self.routes4), "current": 1})
        if req.path == "/api/quagga/diagnostics/search_generalroute6":
            return js({"rows": [], "total": 0})
        if req.path == "/restconf/data/Cisco-IOS-XE-bgp-oper:bgp-state-data/neighbors":
            return js({"Cisco-IOS-XE-bgp-oper:neighbors": {"neighbor": [
                {"afi-safi": "ipv4-unicast", "vrf-name": "default", "neighbor-id": peer, "description": p.get("desc", ""), "as": p["remoteAs"],
                 "connection": {"state": "fsm-established" if p["state"] == "Established" else "fsm-active"},
                 "prefix-activity": {"received": {"current-prefixes": p["pfxRcd"]}}, "up-time": p["peerUptime"]}
                for peer, p in self.peers.items()]}})
        if req.path == "/restconf/data/ietf-routing:routing-state/routing-instance=default/ribs/rib=ipv4-default/routes":
            src = {"connected": "ietf-routing:direct", "static": "ietf-routing:static", "bgp": "Cisco-IOS-XE-native:bgp", "ospf": "ietf-ospf:ospfv2"}
            return js({"ietf-routing:routes": {"route": [
                {"destination-prefix": r["prefix"], "source-protocol": src[r["protocol"]], "active": [None],
                 "next-hop": {"next-hop-address": r.get("ip", ""), "outgoing-interface": r.get("interfaceName", "")}}
                for r in self.routes4 if r["selected"]]}})
        return Response("", 404)


class FakeTraefik:
    """Traefik's API: /api/version, /api/http/routers, /api/http/services (paged, like Traefik)."""

    def __init__(self, routers=None, services=None, auth=None):
        self.routers = routers or []
        self.services = services or []
        self.auth = auth  # (user, password) or None
        self.srv = FakeServer(self.handle)
        self.url = self.srv.url

    def handle(self, req: Request):
        if self.auth and (req.authorization is None or (req.authorization.username, req.authorization.password) != self.auth):
            return Response("unauthorized", 401)
        rows = {"/api/http/routers": self.routers, "/api/http/services": self.services}.get(req.path)
        if req.path == "/api/version":
            return _json({"Version": "3.4.0", "Codename": "chaource"})
        if rows is None:
            return Response("not found", 404)
        per, page = int(req.args.get("per_page", 100)), int(req.args.get("page", 1))
        resp = _json(rows[(page - 1) * per: page * per])
        resp.headers["X-Next-Page"] = str(page + 1 if page * per < len(rows) else 1)
        return resp


class FakeDocker:
    """Docker Engine API: /version, /containers/json?all=true."""

    def __init__(self, containers=None, token=None):
        self.containers = containers or []
        self.token = token
        self.srv = FakeServer(self.handle)
        self.url = self.srv.url

    def handle(self, req: Request):
        if self.token and req.headers.get("Authorization") != f"Bearer {self.token}":
            return Response("unauthorized", 401)
        if req.path == "/version":
            return _json({"Version": "27.3.1", "ApiVersion": "1.47"})
        if req.path == "/containers/json":
            rows = self.containers if req.args.get("all") == "true" else [c for c in self.containers if c["State"] == "running"]
            return _json(rows)
        return Response("not found", 404)


def docker_container(name, image, state="running", ports=(), labels=None, networks=None, cid=None):
    return {"Id": cid or uuid.uuid4().hex, "Names": [f"/{name}"], "Image": image, "State": state,
            "Status": "Up 3 days" if state == "running" else "Exited (0) 2 hours ago",
            "Ports": [dict(p) for p in ports], "Labels": labels or {},
            "NetworkSettings": {"Networks": networks or {"bridge": {"IPAddress": "172.17.0.2", "MacAddress": "02:42:ac:11:00:02"}}}}


class FakeSnmpAgent:
    """An SNMP v2c agent on 127.0.0.1 serving a fixed MIB {oid: (tag, raw bytes)}: GET, GETNEXT,
    GETBULK. Built on labdex.snmp's encoding; the encoding itself is checked against net-snmp
    in test_v1200 when snmpd is installed."""

    def __init__(self, mib: dict, community: str = "public"):
        import socket
        import threading
        from labdex import snmp as s
        self.s, self.community = s, community
        self.mib = dict(sorted(mib.items(), key=lambda kv: tuple(int(x) for x in kv[0].split("."))))
        self.keys = list(self.mib)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        self.stop = False
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def close(self):
        self.stop = True
        self.t.join(2)
        self.sock.close()

    def _after(self, oid):
        k = tuple(int(x) for x in oid.split("."))
        return next((o for o in self.keys if tuple(int(x) for x in o.split(".")) > k), None)

    def _bind(self, oid, have):
        s = self.s
        if have is None:
            return s.seq(s.enc_oid(oid), s.tlv(s.END_OF_MIB, b""))
        tag, raw = self.mib[have]
        return s.seq(s.enc_oid(have), s.tlv(tag, raw))

    def _serve(self):
        s = self.s
        while not self.stop:
            try:
                data, addr = self.sock.recvfrom(65535)
            except OSError:
                continue
            try:
                _, v, _ = s.decode(data)
                ver, comm, p = s.items(v)
                if comm[1].decode() != self.community:
                    continue  # like a real agent: silence
                kind, rid, a, b, binds = s.parse_pdu(s.tlv(p[0], p[1]))
                out = []
                if kind == s.GET:
                    for oid, _ in binds:
                        out.append(s.seq(s.enc_oid(oid), s.tlv(s.NO_SUCH_OBJECT, b"")) if oid not in self.mib else self._bind(oid, oid))
                elif kind == s.GETNEXT:
                    out = [self._bind(o, self._after(o)) for o, _ in binds]
                elif kind == s.GETBULK:
                    cur = binds[0][0]
                    for _ in range(max(1, b)):
                        nxt = self._after(cur)
                        out.append(self._bind(cur, nxt))
                        if nxt is None:
                            break
                        cur = nxt
                resp = s.seq(s.enc_int(rid), s.enc_int(0), s.enc_int(0), s.seq(*out), tag=s.RESPONSE)
                self.sock.sendto(s.seq(s.enc_int(1), s.tlv(s.OCTETS, comm[1]), resp), addr)
            except Exception:  # a fake: never die
                continue
