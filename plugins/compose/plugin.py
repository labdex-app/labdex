"""Compose stacks as code: plan against the Docker hosts, deploy with Ansible after an approval.

plan_deploy runs read_stacks.py on each host (read-only) to see the stack folders, and compares
them with the stacks kept in labdex (labdex.composefiles). apply_deploy writes compose.yaml, .env
and the .labdex marker of every planned stack and runs docker compose; secrets in env are vault
references that labdex fills in only for the Ansible run, and only into the .env file.
"""
import base64
import json
import shutil
import tempfile
from pathlib import Path

from labdex import ansiblevault, composefiles
from labdex.plugins.base import Change, PluginError
from labdex.plugins.base import Plugin as Base


def read_play(root: str, out_dir: str, script: str) -> list[dict]:
    return [{"name": "labdex: read the stacks", "hosts": "all", "become": True, "gather_facts": False, "tasks": [
        {"name": "read the stack folders", "ansible.builtin.script": {"cmd": f"{script} {root}", "executable": "python3"},
         "register": "labdex_stacks", "changed_when": False},
        {"name": "keep them for labdex", "ansible.builtin.copy": {"content": "{{ labdex_stacks.stdout }}",
                                                                  "dest": f"{out_dir}/{{{{ inventory_hostname }}}}.json", "mode": "0600"},
         "delegate_to": "localhost", "become": False, "changed_when": False},
    ]}]


def deploy_play(root: str, pull: bool) -> list[dict]:
    """One play over the planned hosts; each task loops over labdex_items and acts on this host's."""
    up = "item.host == inventory_hostname and item.op in ['create', 'update']"
    down = "item.host == inventory_hostname and item.op == 'delete'"
    d = root.rstrip("/") + "/{{ item.name }}"
    loop = {"loop": "{{ labdex_items }}", "loop_control": {"label": "{{ item.host }}/{{ item.name }}"}}
    tasks = [
        {"name": "stack folder", "ansible.builtin.file": {"path": d, "state": "directory", "mode": "0750"}, "when": up, **loop},
        # base64: the compose file reaches the host as written, never through Ansible's templating
        {"name": "compose.yaml", "ansible.builtin.copy": {"content": "{{ item.compose_b64 | b64decode }}", "dest": f"{d}/compose.yaml", "mode": "0640"},
         "when": up, **loop},
        # the env too, already quoted for compose: a value is never read as a template, and no_log keeps it out of the log
        {"name": ".env", "ansible.builtin.copy": {"content": "{{ item.env_b64 | b64decode }}", "dest": f"{d}/.env", "mode": "0600"},
         "when": up, "no_log": True, **loop},
        {"name": "marker", "ansible.builtin.copy": {"content": "{{ item.marker | to_json }}", "dest": f"{d}/.labdex", "mode": "0640"},
         "when": up, **loop},
        *([{"name": "docker compose pull", "ansible.builtin.command": {"cmd": "docker compose -p {{ item.name }} pull", "chdir": d},
            "when": up, **loop}] if pull else []),
        {"name": "docker compose up", "ansible.builtin.command": {"cmd": "docker compose -p {{ item.name }} up -d --remove-orphans", "chdir": d},
         "when": up, **loop},
        {"name": "only stacks labdex wrote are removed", "ansible.builtin.stat": {"path": f"{d}/.labdex"}, "register": "labdex_marker",
         "when": down, **loop},
        {"name": "docker compose down", "ansible.builtin.command": {"cmd": "docker compose -p {{ item.item.name }} down",
                                                                    "chdir": root.rstrip("/") + "/{{ item.item.name }}"},
         "loop": "{{ labdex_marker.results }}", "when": "item.stat is defined and item.stat.exists", "loop_control": {"label": "{{ item.item.name }}"}},
        {"name": "remove the folder", "ansible.builtin.file": {"path": root.rstrip("/") + "/{{ item.item.name }}", "state": "absent"},
         "loop": "{{ labdex_marker.results }}", "when": "item.stat is defined and item.stat.exists", "loop_control": {"label": "{{ item.item.name }}"}},
    ]
    return [{"name": "labdex: deploy stacks", "hosts": "all", "become": True, "gather_facts": False, "tasks": tasks}]


class Plugin(Base):
    def _stacks(self, only=None) -> list[dict]:
        try:
            stacks = composefiles.parse(self.settings.get("stacks") or "")
        except composefiles.StackError as exc:
            raise PluginError(f"stacks: {exc}") from None
        hosts = {h["name"] for h in self._machines()}
        outside = sorted({s["host"] for s in stacks} - hosts)
        if outside:
            raise PluginError(f"not covered by this integration (or not in the inventory): {', '.join(outside)}")
        if only:
            unknown = set(only) - {s["name"] for s in stacks}
            if unknown:
                raise PluginError(f"no stack {', '.join(sorted(unknown))}")
            stacks = [s for s in stacks if s["name"] in only]
        return stacks

    def _machines(self) -> list[dict]:
        return [h for h in self.ctx.hosts() if h.get("kind") != "vip"]

    def _inventory(self, names: set[str]) -> dict:
        inv = self.ctx.inventory.ansible_inventory()
        hv = inv["all"]["hosts"]
        for n in list(hv):
            if n not in names:
                del hv[n]
        for h in self._machines():
            ms = h.get("module_settings") or {}
            if h["name"] in hv and ms.get("ssh_user"):
                hv[h["name"]]["ansible_user"] = ms["ssh_user"]
            if h["name"] in hv and ms.get("port"):
                hv[h["name"]]["ansible_port"] = ms["port"]
        for g in inv["all"].get("children", {}).values():
            g["hosts"] = {n: None for n in g.get("hosts") or {} if n in names}
        return inv

    @property
    def root(self) -> str:
        r = (self.settings.get("root") or "/opt/stacks").strip()
        if not r.startswith("/") or ".." in r.split("/") or r.rstrip("/") in ("", "/etc", "/usr", "/var", "/root", "/home"):
            raise PluginError("the folder for stacks: an absolute path of its own, like /opt/stacks")
        return r.rstrip("/")

    def _read(self, hosts: set[str]) -> dict[str, dict]:
        """host/name → {marker, compose} for every stack folder on the hosts."""
        with tempfile.TemporaryDirectory(prefix="labdex-compose-") as tmp:
            res = self.ctx.ansible(inline=read_play(self.root, tmp, str(Path(self.ctx.plugin_dir) / "read_stacks.py")),
                                   inventory_override=self._inventory(hosts), limit=",".join(sorted(hosts)))
            seen, missing = {}, []
            for h in sorted(hosts):
                f = Path(tmp) / f"{h}.json"
                if not f.is_file():
                    missing.append(h)
                    continue
                try:
                    for name, entry in (json.loads(f.read_text() or "{}") or {}).items():
                        seen[f"{h}/{name}"] = entry
                except ValueError:
                    missing.append(h)
        if missing or not res.ok:
            raise PluginError(f"couldn't read the stacks on {', '.join(missing) or 'every host'} "
                              f"(failed={res.failed}, unreachable={res.unreachable}); nothing was planned")
        return seen

    # ---------------------------------------------------------------- actions
    def test(self):
        stacks = self._stacks()
        if shutil.which("ansible-playbook") is None:
            raise PluginError("ansible-playbook isn't installed in this labdex instance (build the image with ANSIBLE=1)")
        return f"OK: {len(stacks)} stack(s) on {len({s['host'] for s in stacks})} host(s)"

    def run_preview(self, params):
        stacks = self._stacks()
        for s in stacks:
            self.log(f"--- {s['host']}:{self.root}/{s['name']}/compose.yaml")
            for line in composefiles.compose_text(s).splitlines():
                self.log(line)
            if s["env"]:
                self.log(".env: " + ", ".join(f"{k}=" + ("(from the vault)" if isinstance(v, dict) else str(v)) for k, v in s["env"].items()))
        return {"stacks": [f"{s['host']}/{s['name']}" for s in stacks]}

    def plan_deploy(self, params):
        only = [str(x).strip() for x in params.get("stacks") or [] if str(x).strip()]
        stacks = self._stacks(only)
        hosts = {s["host"] for s in stacks} if only else {h["name"] for h in self._machines()} & ({s["host"] for s in stacks} | self._deployed_hosts())
        if not hosts:
            self.log("no stacks")
            return []
        seen = self._read(hosts)
        if only:
            seen = {k: v for k, v in seen.items() if k.split("/", 1)[1] in only}
        missing = sorted({n for s in stacks for n in ansiblevault.missing(s["env"])})
        if missing:
            raise PluginError(f"not in the vault: {', '.join(missing)}")
        ops = composefiles.plan(stacks, seen, bool(self.settings.get("allow_remove")))
        conflicts = [f"{o['host']}/{o['name']}" for o in ops if o["op"] == "conflict"]
        if conflicts:
            raise PluginError(f"folders labdex didn't write: {', '.join(conflicts)}; rename those stacks or move the folders")
        for o in ops:
            if o["op"] == "keep":
                self.log(f"{o['host']}/{o['name']}: {o['summary']}")
            elif o["diff"]:
                self.log(o["diff"].rstrip())
        return [Change(o["op"], "stack", f"{o['host']}/{o['name']}", o["summary"], None, {"diff": o["diff"], "env_changed": o["env_changed"]},
                       {"hosts": [o["host"]], "host": o["host"], "name": o["name"]}) for o in ops if o["op"] != "keep"]

    def _deployed_hosts(self) -> set[str]:
        return set(self.ctx.state.get("deployed_hosts") or [])

    def apply_deploy(self, changes, params):
        stacks = {f"{s['host']}/{s['name']}": s for s in self._stacks()}
        items, hosts = [], set()
        for c in changes:
            key = f"{c.data['host']}/{c.data['name']}"
            if c.op == "delete":
                items.append({"op": "delete", "host": c.data["host"], "name": c.data["name"]})
            else:
                s = stacks.get(key)
                if s is None:
                    raise PluginError(f"{key} changed since the plan; plan again")
                try:  # the secrets, for this run only; the .env task keeps them out of the log
                    env = composefiles.env_text(ansiblevault.reveal_all(s["env"]))
                except ansiblevault.VaultError as exc:
                    raise PluginError(f"{key}: {exc}") from None
                items.append({"op": c.op, "host": s["host"], "name": s["name"], "marker": composefiles.marker(s),
                              "compose_b64": base64.b64encode(composefiles.compose_text(s).encode()).decode(),
                              "env_b64": base64.b64encode(env.encode()).decode()})
            hosts.add(c.data["host"])
        if not items:
            return {"deployed": [], "removed": []}
        res = self.ctx.ansible(inline=deploy_play(self.root, bool(self.settings.get("pull", True))), extravars={"labdex_items": items},
                               inventory_override=self._inventory(hosts), limit=",".join(sorted(hosts)))
        if not res.ok:
            raise PluginError(f"the deploy stopped (rc={res.rc}, failed={res.failed}, unreachable={res.unreachable}); see the log")
        self.ctx.set_state("deployed_hosts", sorted(self._deployed_hosts() | {i["host"] for i in items if i["op"] != "delete"}))
        return {"deployed": [f"{i['host']}/{i['name']}" for i in items if i["op"] != "delete"],
                "removed": [f"{i['host']}/{i['name']}" for i in items if i["op"] == "delete"]}
