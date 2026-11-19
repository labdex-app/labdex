"""Patching with Ansible: install the waiting updates on the hosts of a site, reboot where allowed.

plan_patch reads what the hosts last reported (the Patches page) and lists one change per host
that has something waiting; nothing touches a host before a person approved the plan. apply_patch
runs one generated play: upgrade with the host's package manager, reboot if needed and allowed,
then run labdex-patch-report on the host and report its answer back. run_report does only the
last part.

Hosts that are Kubernetes nodes (with the Kubernetes settings filled in) are patched one at a
time after the others: cordon, evict the pods (PodDisruptionBudgets respected), patch and maybe
reboot, wait until the node is Ready, uncordon. A node that can't be drained or doesn't come back
stops the run and stays cordoned, so a person looks at it.
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from labdex.plugins.base import Change, Plugin as Base, PluginError

REBOOT = ("never", "if_needed")


def agent_path() -> Path:
    from labdex.agent import patch_report
    return Path(patch_report.__file__)


def report_tasks(report_dir: str) -> list[dict]:
    """Run labdex-patch-report on the host and keep its answer on this side, one file per host."""
    return [
        {"name": "read the patch state", "ansible.builtin.script": {"cmd": f"{agent_path()} --dry-run --host {{{{ inventory_hostname }}}}",
                                                                    "executable": "python3"},
         "register": "labdex_report", "changed_when": False},
        {"name": "keep it for labdex", "ansible.builtin.copy": {"content": "{{ labdex_report.stdout }}",
                                                                "dest": f"{report_dir}/{{{{ inventory_hostname }}}}.json", "mode": "0600"},
         "delegate_to": "localhost", "become": False, "changed_when": False},
    ]


def patch_play(*, serial: int, autoremove: bool, reboot_hosts: list[str], reboot_timeout: int, report_dir: str) -> list[dict]:
    """One play: upgrade, find out whether a reboot is needed, reboot the hosts allowed to, report."""
    pkg = "ansible_facts.pkg_mgr"
    tasks = [
        {"name": "upgrade (apt)", "ansible.builtin.apt": {"update_cache": True, "upgrade": "dist", "autoremove": autoremove},
         "environment": {"DEBIAN_FRONTEND": "noninteractive"}, "when": f"{pkg} == 'apt'"},
        {"name": "upgrade (dnf)", "ansible.builtin.dnf": {"name": "*", "state": "latest", "update_cache": True},
         "when": f"{pkg} in ['dnf', 'dnf5']"},
        {"name": "upgrade (yum)", "ansible.builtin.yum": {"name": "*", "state": "latest", "update_cache": True}, "when": f"{pkg} == 'yum'"},
        {"name": "unsupported package manager", "ansible.builtin.fail": {"msg": "labdex patches apt, dnf and yum hosts; this one has {{ " + pkg + " }}"},
         "when": f"{pkg} not in ['apt', 'dnf', 'dnf5', 'yum']"},
        {"name": "reboot needed? (Debian, Ubuntu, Proxmox)", "ansible.builtin.stat": {"path": "/var/run/reboot-required"}, "register": "labdex_rr"},
        {"name": "reboot needed? (dnf, yum)", "ansible.builtin.command": "needs-restarting -r", "register": "labdex_nr",
         "changed_when": False, "failed_when": False, "when": f"{pkg} != 'apt'"},
        {"name": "reboot", "ansible.builtin.reboot": {"reboot_timeout": reboot_timeout, "msg": "labdex patch run"},
         "when": ["inventory_hostname in labdex_reboot_hosts",
                  "(labdex_rr.stat.exists | default(false)) or ((labdex_nr.rc | default(0)) == 1)"]},
        *report_tasks(report_dir),
    ]
    return [{"name": "labdex patch run", "hosts": "all", "become": True, "serial": max(1, serial), "max_fail_percentage": 0,
             "vars": {"labdex_reboot_hosts": reboot_hosts}, "tasks": tasks}]


def report_play(report_dir: str) -> list[dict]:
    return [{"name": "labdex patch state", "hosts": "all", "become": False, "gather_facts": False, "tasks": report_tasks(report_dir)}]


class Plugin(Base):
    # ---------------------------------------------------------------- kubernetes
    def _kube(self):
        """An API client for node maintenance, or None when no cluster is set."""
        s = self.settings
        if not (s.get("kube_url") or "").strip():
            return None
        verify = bool(s.get("kube_verify_tls", True))
        if verify and (s.get("kube_ca_pem") or "").strip():
            fd, path = tempfile.mkstemp(prefix="labdex-k8s-ca-", suffix=".pem")
            with os.fdopen(fd, "w") as f:
                f.write(s["kube_ca_pem"].strip() + "\n")
            verify = path
        return self.ctx.http(s["kube_url"].rstrip("/") + "/", verify=verify, timeout=15,
                             headers={"Authorization": f"Bearer {s.get('kube_token', '')}"})

    def _nodes(self, http, hosts: list[dict]) -> dict[str, str]:
        """host name → node name, for the hosts that are nodes of the cluster."""
        names = {h["name"] for h in hosts}
        by_ip = {i.get("ip4"): h["name"] for h in hosts for i in h.get("interfaces") or [] if i.get("ip4")}
        match = self.ctx.match_host()
        out = {}
        for n in (http.get("api/v1/nodes").json() or {}).get("items") or []:
            node = n["metadata"]["name"]
            ips = [a.get("address") for a in (n.get("status") or {}).get("addresses") or [] if a.get("type") == "InternalIP"]
            host = node if node in names else next((by_ip[ip] for ip in ips if ip in by_ip), None) or match(node)
            if host in names:
                out[host] = node
        return out

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _cordon(self, http, node: str, on: bool) -> None:
        http.request("PATCH", f"api/v1/nodes/{node}", json={"spec": {"unschedulable": True if on else None}},
                     headers={"Content-Type": "application/merge-patch+json"})

    def _evictable(self, http, node: str) -> list[dict]:
        pods = (http.get("api/v1/pods", params={"fieldSelector": f"spec.nodeName={node}"}).json() or {}).get("items") or []
        out = []
        for p in pods:
            meta = p.get("metadata") or {}
            if any(o.get("kind") == "DaemonSet" for o in meta.get("ownerReferences") or []):
                continue  # runs on every node anyway
            if "kubernetes.io/config.mirror" in (meta.get("annotations") or {}):
                continue  # static pods belong to the kubelet
            if (p.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
                continue
            out.append(p)
        return out

    def _drain(self, http, node: str) -> None:
        from labdex.plugins.base import PluginError as Err
        self._cordon(http, node, True)
        self.log(f"{node}: cordoned")
        deadline = time.monotonic() + int(self.settings.get("drain_timeout") or 300)
        while True:
            pods = self._evictable(http, node)
            if not pods:
                self.log(f"{node}: drained")
                return
            held = []
            for p in pods:
                meta = p["metadata"]
                body = {"apiVersion": "policy/v1", "kind": "Eviction", "metadata": {"name": meta["name"], "namespace": meta["namespace"]}}
                try:
                    http.post(f"api/v1/namespaces/{meta['namespace']}/pods/{meta['name']}/eviction", json=body)
                except Err as exc:
                    if "HTTP 429" not in str(exc) and "HTTP 404" not in str(exc):
                        raise
                    if "HTTP 429" in str(exc):
                        held.append(f"{meta['namespace']}/{meta['name']}")
            if time.monotonic() > deadline:
                raise Err(f"{node}: couldn't drain it in time ({', '.join(held) or 'pods still terminating'}); "
                          "it stays cordoned")
            self._sleep(5)

    def _wait_ready(self, http, node: str) -> None:
        deadline = time.monotonic() + int(self.settings.get("reboot_timeout") or 900)
        while True:
            n = http.get(f"api/v1/nodes/{node}").json() or {}
            ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in (n.get("status") or {}).get("conditions") or [])
            if ready:
                return
            if time.monotonic() > deadline:
                raise PluginError(f"{node}: not Ready after patching; it stays cordoned")
            self._sleep(10)

    # ---------------------------------------------------------------- hosts
    def _machines(self) -> list[dict]:
        """The hosts this integration applies to, without VIPs and hosts set to be left alone."""
        return [h for h in self.ctx.hosts() if h.get("kind") != "vip" and not (h.get("module_settings") or {}).get("skip")]

    def _inventory(self, hosts: list[dict]) -> dict:
        inv = self.ctx.inventory.ansible_inventory()
        keep = {h["name"] for h in hosts}
        hv = inv["all"]["hosts"]
        for name in list(hv):
            if name not in keep:
                del hv[name]
        for h in hosts:
            ms = h.get("module_settings") or {}
            if h["name"] in hv and ms.get("ssh_user"):
                hv[h["name"]]["ansible_user"] = ms["ssh_user"]
            if h["name"] in hv and ms.get("port"):
                hv[h["name"]]["ansible_port"] = ms["port"]
        for g in inv["all"].get("children", {}).values():
            g["hosts"] = {n: None for n in g.get("hosts") or {} if n in keep}
        return inv

    def _guests(self) -> dict[str, int]:
        """host → how many hosts in the inventory run on it (VMs, containers)."""
        if not hasattr(self, "_guest_count"):
            count: dict[str, int] = {}
            for g in self.ctx.inventory.hosts(include_offline=True):
                if g.get("runs_on"):
                    count[g["runs_on"]] = count.get(g["runs_on"], 0) + 1
            self._guest_count = count
        return self._guest_count

    def _reboot_mode(self, params) -> str:
        mode = (params.get("reboot") or self.settings.get("reboot") or "never").strip()
        if mode not in REBOOT:
            raise PluginError(f"reboot: one of {', '.join(REBOOT)}")
        return mode

    def _may_reboot(self, h: dict, mode: str) -> tuple[bool, str]:
        if mode != "if_needed":
            return False, "reboots are off"
        if (h.get("module_settings") or {}).get("no_reboot"):
            return False, "never rebooted (host setting)"
        guests = self._guests().get(h["name"], 0)
        if guests and not self.settings.get("reboot_hypervisors"):
            return False, f"runs {guests} guest(s); not rebooted"
        return True, ""

    # ---------------------------------------------------------------- report
    def _collect(self, report_dir: Path, names: list[str]) -> dict:
        out = {}
        for n in names:
            f = report_dir / f"{n}.json"
            if not f.is_file():
                continue
            try:
                data = json.loads(f.read_text() or "{}")
            except ValueError:
                self.log(f"{n}: the report isn't JSON")
                continue
            out[n] = {k: v for k, v in data.items() if k not in ("host", "source")}
        return out

    def _report_back(self, report_dir: Path, names: list[str]) -> list[str]:
        results = self._collect(report_dir, names)
        if results:
            self.ctx.report_patches(results, replace=False)
        missing = sorted(set(names) - set(results))
        if missing:
            self.log(f"no report from: {', '.join(missing)}")
        return sorted(results)

    # ---------------------------------------------------------------- actions
    def test(self):
        if shutil.which("ansible-playbook") is None:
            raise PluginError("ansible-playbook isn't installed in this labdex instance (build the image with ANSIBLE=1)")
        hosts = self._machines()
        return f"OK: applies to {len(hosts)} host(s)"

    def run_report(self, params):
        hosts = self._machines()
        if not hosts:
            raise PluginError("this integration applies to no host (check its 'applies to')")
        names = [h["name"] for h in hosts]
        with tempfile.TemporaryDirectory(prefix="labdex-patch-") as tmp:
            res = self.ctx.ansible(inline=report_play(tmp), inventory_override=self._inventory(hosts), limit=",".join(names))
            reported = self._report_back(Path(tmp), names)
        if not reported and not res.ok:
            raise PluginError(f"no host answered (failed={res.failed}, unreachable={res.unreachable})")
        return {"reported": reported, "unreachable": res.unreachable, "failed": res.failed}

    def plan_patch(self, params):
        mode = self._reboot_mode(params)
        only = {str(n).strip() for n in params.get("hosts") or [] if str(n).strip()}
        hosts = self._machines()
        if only:
            unknown = only - {h["name"] for h in hosts}
            if unknown:
                raise PluginError(f"not covered by this integration: {', '.join(sorted(unknown))}")
            hosts = [h for h in hosts if h["name"] in only]
        states = self.ctx.inventory.patches()
        http = self._kube()
        nodes = self._nodes(http, hosts) if http is not None else {}
        changes = []
        for h in hosts:
            s = states.get(h["name"]) or {"state": "unknown", "pending": 0, "security": 0, "reboot": False}
            fresh = s["state"] not in ("unknown", "stale", "error")
            if not fresh and not (only or self.settings.get("include_unreported")):
                continue
            if params.get("security_only") and not s["security"]:
                continue
            may, why = self._may_reboot(h, mode)
            if fresh and not s["pending"] and not (s["reboot"] and may):
                continue  # nothing waiting, and no reboot we'd do
            if fresh:
                what = f"{s['pending']} update(s)" + (f", {s['security']} security" if s["security"] else "") if s["pending"] else "no updates"
            else:
                what = "no recent report: installs whatever is waiting"
            reboot = ("reboots if needed" if may else why) if mode == "if_needed" else ("needs a reboot; reboots are off" if s["reboot"] else "")
            node = nodes.get(h["name"])
            drain = f"Kubernetes node {node}: drained first, uncordoned once Ready" if node else ""
            changes.append(Change("ansible", "patch", h["name"], "; ".join(x for x in (what, reboot, drain) if x),
                                  {"pending": s["pending"], "security": s["security"], "reboot": s["reboot"]},
                                  {"reboot": may, **({"node": node} if node else {})},
                                  {"hosts": [h["name"]], "reboot": may, **({"node": node} if node else {})}))
        if not changes:
            self.log("no host has updates waiting" + ("" if only else " (hosts without a recent report are left out)"))
        return changes

    def apply_patch(self, changes, params):
        by_name = {h["name"]: h for h in self._machines()}
        names = [n for c in changes for n in c.data["hosts"] if n in by_name]
        gone = sorted({n for c in changes for n in c.data["hosts"]} - set(names))
        if gone:
            self.log(f"left out, no longer covered: {', '.join(gone)}")
        if not names:
            raise PluginError("none of the planned hosts is covered by this integration any more")
        reboot_hosts = [n for c in changes if c.data.get("reboot") for n in c.data["hosts"] if n in by_name]
        planned_nodes = {n: c.data["node"] for c in changes if c.data.get("node") for n in c.data["hosts"] if n in by_name}
        http = self._kube() if planned_nodes else None
        if planned_nodes and http is None:
            raise PluginError("the plan drains Kubernetes nodes, but the Kubernetes settings are gone; plan again")
        others = [n for n in names if n not in planned_nodes]
        patched, reported, changed = [], [], 0
        if others:
            res, rep = self._run(by_name, others, reboot_hosts)
            reported += rep
            changed += res.changed
            if not res.ok:
                raise PluginError(f"the patch run stopped (rc={res.rc}, failed={res.failed}, unreachable={res.unreachable}); "
                                  f"reported back: {', '.join(reported) or 'none'}; hosts after the failing one weren't touched"
                                  + ("; no Kubernetes node was touched" if planned_nodes else ""))
            patched += others
        for host in [n for n in names if n in planned_nodes]:  # one node at a time
            node = planned_nodes[host]
            try:
                self._drain(http, node)
            except PluginError as exc:
                raise PluginError(f"{exc}; patched so far: {', '.join(patched) or 'none'}") from None
            res, rep = self._run(by_name, [host], reboot_hosts)
            reported += rep
            changed += res.changed
            if not res.ok:
                raise PluginError(f"patching {host} failed (failed={res.failed}, unreachable={res.unreachable}); node {node} stays cordoned; "
                                  f"patched so far: {', '.join(patched) or 'none'}")
            self._wait_ready(http, node)
            self._cordon(http, node, False)
            self.log(f"{node}: Ready and schedulable again")
            patched.append(host)
        return {"patched": patched, "reboot_allowed": reboot_hosts, "changed": changed, "reported": reported,
                "nodes": [planned_nodes[n] for n in patched if n in planned_nodes]}

    def _run(self, by_name, names, reboot_hosts):
        s = self.settings
        with tempfile.TemporaryDirectory(prefix="labdex-patch-") as tmp:
            play = patch_play(serial=int(s.get("serial") or 1), autoremove=bool(s.get("autoremove")),
                              reboot_hosts=[n for n in reboot_hosts if n in names],
                              reboot_timeout=int(s.get("reboot_timeout") or 900), report_dir=tmp)
            res = self.ctx.ansible(inline=play, inventory_override=self._inventory([by_name[n] for n in names]), limit=",".join(names))
            return res, self._report_back(Path(tmp), names)
