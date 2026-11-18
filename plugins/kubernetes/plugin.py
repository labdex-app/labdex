"""Kubernetes workloads & Flux, read-only: what runs in a cluster and whether Flux keeps it in step.

Only GET requests. Flux's API groups are found by discovery, so a cluster without Flux (or a
token that may not read it) still shows its workloads.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path

from labdex.plugins.base import Change, PluginError
from labdex.plugins.base import Plugin as Base

WORKLOADS = (("Deployment", "deployments"), ("StatefulSet", "statefulsets"), ("DaemonSet", "daemonsets"))
# group → [(kind, plural)]; the version comes from discovery (the cluster's preferred one)
FLUX = {
    "kustomize.toolkit.fluxcd.io": [("Kustomization", "kustomizations")],
    "helm.toolkit.fluxcd.io": [("HelmRelease", "helmreleases")],
    "source.toolkit.fluxcd.io": [("GitRepository", "gitrepositories"), ("OCIRepository", "ocirepositories"),
                                 ("HelmRepository", "helmrepositories")],
}
APPLIERS = ("Kustomization", "HelmRelease")


def _ready(obj: dict) -> tuple[str, str, str]:
    """(True|False|Unknown, reason, message) of the Ready condition."""
    c = next((c for c in (obj.get("status") or {}).get("conditions") or [] if c.get("type") == "Ready"), None)
    if c is None:
        return "Unknown", "", ""
    return str(c.get("status") or "Unknown"), str(c.get("reason") or ""), str(c.get("message") or "")[:300]


def flux_object(kind: str, obj: dict) -> dict:
    md, spec, st = obj.get("metadata") or {}, obj.get("spec") or {}, obj.get("status") or {}
    ready, reason, message = _ready(obj)
    out = {"kind": kind, "namespace": md.get("namespace", ""), "name": md.get("name", ""), "ready": ready, "reason": reason,
           "message": message, "suspended": bool(spec.get("suspend")), "revision": "", "source": "", "detail": ""}
    if kind == "Kustomization":
        ref = spec.get("sourceRef") or {}
        out["revision"] = st.get("lastAppliedRevision") or ""
        out["source"] = f"{ref.get('kind', '')}/{ref.get('namespace') or out['namespace']}/{ref.get('name', '')}" if ref else ""
        out["detail"] = spec.get("path") or "./"
    elif kind == "HelmRelease":
        chart = ((spec.get("chart") or {}).get("spec") or {})
        ref = chart.get("sourceRef") or spec.get("chartRef") or {}
        history = st.get("history") or []
        out["revision"] = (history[0].get("chartVersion") if history else "") or st.get("lastAttemptedRevision") or ""
        out["source"] = f"{ref.get('kind', '')}/{ref.get('namespace') or out['namespace']}/{ref.get('name', '')}" if ref else ""
        out["detail"] = " ".join(x for x in (chart.get("chart") or "", chart.get("version") or "") if x)
    else:
        out["revision"] = (st.get("artifact") or {}).get("revision") or ""
        ref = spec.get("ref") or {}
        branch = ref.get("branch") or ref.get("tag") or ref.get("semver") or ""
        out["detail"] = " ".join(x for x in (spec.get("url") or "", branch) if x)
    out["state"] = "suspended" if out["suspended"] else {"True": "ok", "False": "failed"}.get(ready, "pending")
    return out


def workload(kind: str, obj: dict) -> dict:
    md, spec, st = obj.get("metadata") or {}, obj.get("spec") or {}, obj.get("status") or {}
    if kind == "DaemonSet":
        want, ready = int(st.get("desiredNumberScheduled") or 0), int(st.get("numberReady") or 0)
    else:
        want, ready = int(spec.get("replicas", 1) or 0), int(st.get("readyReplicas") or 0)
    containers = ((spec.get("template") or {}).get("spec") or {}).get("containers") or []
    state = "stopped" if want == 0 else "ok" if ready >= want else "down" if ready == 0 else "degraded"
    return {"kind": kind, "namespace": md.get("namespace", ""), "name": md.get("name", ""), "want": want, "ready": ready, "state": state,
            "images": [c.get("image", "") for c in containers if c.get("image")]}


REPO_RE = re.compile(r"^(https://[^\s@]+|file://\S+|/\S+)$")  # no user:password@: the token is a setting
AUTHOR = ("labdex", "labdex@localhost")


def key(o: dict) -> str:
    return f"{o['kind']}/{o['namespace']}/{o['name']}"


class Plugin(Base):
    # ---------------------------------------------------------------- reading
    def _api(self):
        s = self.settings
        if not s.get("url"):
            raise PluginError("set the API server first")
        verify = bool(s.get("verify_tls", True))
        if verify and (s.get("ca_pem") or "").strip():
            fd, path = tempfile.mkstemp(prefix="labdex-k8s-ca-", suffix=".pem")
            with os.fdopen(fd, "w") as f:
                f.write(s["ca_pem"].strip() + "\n")
            self._ca_file = path
            verify = path
        return self.ctx.http(s["url"].rstrip("/") + "/", verify=verify, timeout=15, headers={"Authorization": f"Bearer {s.get('token', '')}"})

    def _done(self):
        p = getattr(self, "_ca_file", None)
        if p:
            os.unlink(p)
            self._ca_file = None

    @staticmethod
    def _optional(http, path: str):
        """GET that answers None for an API the cluster doesn't have or the token may not read."""
        try:
            return http.get(path).json() or {}
        except PluginError as exc:
            if "HTTP 404" in str(exc) or "access denied" in str(exc):
                return None
            raise

    def read(self) -> dict:
        http = self._api()
        notes = []
        try:
            version = (http.get("version").json() or {}).get("gitVersion", "")
            nodes = (http.get("api/v1/nodes").json() or {}).get("items") or []
            ignore = set((self.settings.get("ignore_namespaces") or "").split())
            loads = []
            for kind, plural in WORKLOADS:
                data = self._optional(http, f"apis/apps/v1/{plural}")
                if data is None:
                    notes.append(f"can't read {plural}")
                    continue
                loads += [w for w in (workload(kind, o) for o in data.get("items") or []) if w["namespace"] not in ignore]
            flux = []
            for group, kinds in FLUX.items():
                disc = self._optional(http, f"apis/{group}")
                if disc is None:
                    continue
                gv = ((disc.get("preferredVersion") or {}).get("groupVersion")) or ""
                if not gv:
                    continue
                for kind, plural in kinds:
                    data = self._optional(http, f"apis/{gv}/{plural}")
                    if data is None:
                        notes.append(f"can't read {plural}")
                        continue
                    flux += [flux_object(kind, o) for o in data.get("items") or []]
        finally:
            self._done()
        ready_nodes = sum(1 for n in nodes if _ready(n)[0] == "True")
        return {"version": version, "nodes": len(nodes), "ready_nodes": ready_nodes, "flux_installed": bool(flux),
                "workloads": sorted(loads, key=key), "flux": sorted(flux, key=key), "notes": notes}

    # ---------------------------------------------------------------- actions
    @property
    def cluster(self) -> str:
        return (self.settings.get("cluster") or "").strip() or self.ctx.name

    def test(self):
        st = self.read()
        flux = f", Flux: {sum(1 for o in st['flux'] if o['kind'] in APPLIERS)} kustomizations/releases" if st["flux"] else ", no Flux objects"
        return f"{st['version']}: {st['nodes']} node(s), {len(st['workloads'])} workload(s){flux}"

    def run_poll(self, params):
        st = self.read()
        before = self.ctx.state.get("cluster") or {}
        was_bad = {key(o) for o in before.get("flux", []) if o["state"] == "failed"} | \
                  {key(w) for w in before.get("workloads", []) if w["state"] in ("down", "degraded")}
        first = not before
        for o in st["flux"]:
            if o["state"] == "failed" and key(o) not in was_bad and not first:
                self.ctx.notify("flux.failed", f"{self.cluster}: {o['kind']} {o['namespace']}/{o['name']} failed",
                                f"{o['reason']}: {o['message']}", f"/integrations/{self.ctx.name}?tab=cluster",
                                {"cluster": self.cluster, **{k: o[k] for k in ("kind", "namespace", "name", "reason", "revision")}})
        for w in st["workloads"]:
            if w["state"] in ("down", "degraded") and key(w) not in was_bad and not first:
                self.ctx.notify("workload.degraded", f"{self.cluster}: {w['kind']} {w['namespace']}/{w['name']} has {w['ready']} of {w['want']} ready",
                                "", f"/integrations/{self.ctx.name}?tab=cluster",
                                {"cluster": self.cluster, **{k: w[k] for k in ("kind", "namespace", "name", "ready", "want")}})
        for n in st["notes"]:
            self.log(n)
        self.ctx.set_state("cluster", st)
        return {"workloads": len(st["workloads"]), "not_ready": sum(1 for w in st["workloads"] if w["state"] in ("down", "degraded")),
                "flux": len(st["flux"]), "flux_failed": sum(1 for o in st["flux"] if o["state"] == "failed")}

    # ---------------------------------------------------------------- views
    def page_cluster(self, args):
        st = self.ctx.state.get("cluster") or {}
        loads, flux = st.get("workloads", []), st.get("flux", [])
        bad_loads = [w for w in loads if w["state"] in ("down", "degraded")]
        failed = [o for o in flux if o["state"] == "failed"]
        tone = {"ok": "", "failed": "err", "down": "err", "degraded": "warn", "pending": "warn", "suspended": "warn", "stopped": ""}
        order = {"failed": 0, "down": 0, "degraded": 1, "pending": 2, "suspended": 3, "ok": 4, "stopped": 5}
        appliers = sorted((o for o in flux if o["kind"] in APPLIERS), key=lambda o: (order[o["state"]], key(o)))
        sources = [o for o in flux if o["kind"] not in APPLIERS]
        return {"summary": [{"label": "cluster", "value": self.cluster},
                            {"label": "version", "value": st.get("version", "?")},
                            {"label": "nodes ready", "value": f"{st.get('ready_nodes', 0)} of {st.get('nodes', 0)}",
                             "tone": "ok" if st and st.get("ready_nodes") == st.get("nodes") else "warn"},
                            {"label": "workloads not ready", "value": len(bad_loads), "tone": "warn" if bad_loads else "ok"},
                            {"label": "Flux failing", "value": len(failed), "tone": "err" if failed else "ok"}],
                "sections": [
                    {"title": "Flux", "type": "table",
                     "columns": [{"key": "state", "label": "State"}, {"key": "kind", "label": "Kind"}, {"key": "ref", "label": "Name"},
                                 {"key": "revision", "label": "Revision"}, {"key": "source", "label": "Source"},
                                 {"key": "detail", "label": "Path / chart"}, {"key": "message", "label": "Message"}],
                     "rows": [o | {"ref": f"{o['namespace']}/{o['name']}", "_tone": tone[o["state"]]} for o in appliers],
                     "empty": "No Flux Kustomizations or HelmReleases (or not read yet: run “Read workloads and Flux”)."},
                    {"title": "Sources", "type": "table",
                     "columns": [{"key": "state", "label": "State"}, {"key": "kind", "label": "Kind"}, {"key": "ref", "label": "Name"},
                                 {"key": "detail", "label": "URL"}, {"key": "revision", "label": "Revision"}, {"key": "message", "label": "Message"}],
                     "rows": [o | {"ref": f"{o['namespace']}/{o['name']}", "_tone": tone[o["state"]]} for o in sources],
                     "empty": "No Flux sources."},
                    {"title": "Workloads", "type": "table",
                     "columns": [{"key": "state", "label": "State"}, {"key": "kind", "label": "Kind"}, {"key": "ref", "label": "Name"},
                                 {"key": "replicas", "label": "Ready"}, {"key": "image", "label": "Images"}],
                     "rows": [w | {"ref": f"{w['namespace']}/{w['name']}", "replicas": f"{w['ready']}/{w['want']}", "image": ", ".join(w["images"]),
                                   "_tone": tone[w["state"]]} for w in sorted(loads, key=lambda w: (order[w["state"]], key(w)))],
                     "empty": "No workloads."}],
                "note": "Read-only: labdex never changes the cluster." + (" " + "; ".join(st["notes"]) if st.get("notes") else "")}

    def widget_cluster(self, host):
        st = self.ctx.state.get("cluster")
        if not st:
            return {"kind": "list", "items": [], "sub": "run “Read workloads and Flux”"}
        bad = [o for o in st["flux"] if o["state"] == "failed"] + [w for w in st["workloads"] if w["state"] in ("down", "degraded")]
        items = [{"label": f"{o['kind']} {o['namespace']}/{o['name']}", "state": "down",
                  "detail": o.get("reason") or f"{o.get('ready')}/{o.get('want')} ready"} for o in bad[:8]]
        if not items:
            items = [{"label": "everything ready", "state": "up",
                      "detail": f"{len(st['workloads'])} workloads, {sum(1 for o in st['flux'] if o['kind'] in APPLIERS)} Flux objects"}]
        return {"kind": "status", "items": items, "link": f"/integrations/{self.ctx.name}?tab=cluster",
                "sub": f"{self.cluster} · {st.get('version', '')}"}

    # ---------------------------------------------------------------- Flux files as code
    def _flux_cfg(self) -> dict:
        s = self.settings
        url = (s.get("flux_repo") or "").strip()
        if not url:
            raise PluginError("set the Flux repository first (settings › Flux files)")
        if not REPO_RE.match(url):
            raise PluginError("the Flux repository: an https:// address without user:password, or a local path")
        branch = (s.get("flux_branch") or "main").strip()
        folder = (s.get("flux_path") or "clusters/{cluster}/labdex").replace("{cluster}", self.cluster).strip("/")
        if not folder or ".." in folder.split("/"):
            raise PluginError("the folder for labdex's files: a relative path in the repository")
        return {"url": url, "branch": branch, "path": folder, "token": s.get("flux_token") or "",
                "namespace": (s.get("flux_namespace") or "flux-system").strip(),
                "work": "labdex/flux-" + re.sub(r"[^A-Za-z0-9._-]+", "-", self.cluster).strip("-.")}

    def _apps(self) -> list[dict]:
        from labdex import fluxfiles
        try:
            return fluxfiles.parse(self.settings.get("apps") or "")
        except fluxfiles.AppError as exc:
            raise PluginError(f"apps: {exc}") from None

    def _render(self, cfg_ns: str = "flux-system") -> dict[str, str]:
        from labdex import fluxfiles
        return fluxfiles.render(self._apps(), f"integration {self.ctx.name}, cluster {self.cluster}", cfg_ns)

    def _git(self, cfg: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true", "LC_ALL": "C"}
        if cfg["token"] and cfg["url"].startswith("https://"):
            basic = base64.b64encode(f"oauth2:{cfg['token']}".encode()).decode()
            # in the environment, never on the command line or in .git/config
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader", GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
        d = self._workdir()
        try:
            r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, env=env, timeout=120)
        except subprocess.TimeoutExpired:
            raise PluginError(f"git {args[0]} took too long") from None
        if check and r.returncode:
            raise PluginError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:400]}")
        return r

    def _workdir(self) -> Path:
        return Path(self.ctx.data_dir) / "flux" / self.ctx.name

    def _fetch(self, cfg: dict) -> str:
        """The live branch's head after a fetch; labdex's working copy never checks it out for writing."""
        d = self._workdir()
        if not (d / ".git").exists():
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
            self._git(cfg, "init", "-q")
        self._git(cfg, "remote", "remove", "origin", check=False)
        self._git(cfg, "remote", "add", "origin", cfg["url"])
        self._git(cfg, "fetch", "-q", "--depth", "20", "origin", f"+refs/heads/{cfg['branch']}:refs/remotes/origin/{cfg['branch']}")
        return self._git(cfg, "rev-parse", f"refs/remotes/origin/{cfg['branch']}").stdout.strip()

    def _files_at(self, cfg: dict, commit: str) -> dict[str, str]:
        r = self._git(cfg, "ls-tree", "--name-only", f"{commit}:{cfg['path']}", check=False)
        out = {}
        for name in r.stdout.split() if r.returncode == 0 else []:
            if name.endswith((".yaml", ".yml")):
                out[name] = self._git(cfg, "show", f"{commit}:{cfg['path']}/{name}").stdout
        return out

    def run_flux_preview(self, params):
        files = self._render((self.settings.get("flux_namespace") or "flux-system").strip())
        for name, text in files.items():
            self.log(f"--- {name}")
            for line in text.splitlines():
                self.log(line)
        return {"files": sorted(files), "folder": (self.settings.get("flux_path") or "clusters/{cluster}/labdex").replace("{cluster}", self.cluster)}

    def plan_flux_files(self, params):
        from labdex import fluxfiles
        cfg = self._flux_cfg()
        want = self._render(cfg["namespace"])
        head = self._fetch(cfg)
        diffs = fluxfiles.diff(want, self._files_at(cfg, head))
        conflicts = [d["file"] for d in diffs if d["op"] == "conflict"]
        if conflicts:
            raise PluginError(f"{cfg['path']}: {', '.join(conflicts)} exist and weren't written by labdex; rename the app or move the file")
        for d in diffs:
            self.log(d["diff"].rstrip() or f"{d['op']} {d['file']}")
        return [Change(d["op"], "file", f"{cfg['path']}/{d['file']}", d["summary"], None, {"diff": d["diff"]},
                       {"file": d["file"], "content": want.get(d["file"]), "base": head}) for d in diffs]

    def apply_flux_files(self, changes, params):
        cfg = self._flux_cfg()
        head = self._fetch(cfg)
        base = changes[0].data["base"] if changes else head
        if base != head:
            raise PluginError(f"{cfg['branch']} moved since the plan ({base[:10]} → {head[:10]}); plan again")
        self._git(cfg, "checkout", "-q", "--detach", "--force", head)
        d = self._workdir() / cfg["path"]
        for c in changes:
            f = d / c.data["file"]
            if c.data["content"] is None:
                self._git(cfg, "rm", "-q", "--", f"{cfg['path']}/{c.data['file']}")
            else:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(c.data["content"], encoding="utf-8")
                self._git(cfg, "add", "--", f"{cfg['path']}/{c.data['file']}")
        message = f"labdex: Flux apps of cluster {self.cluster}"
        self._git(cfg, "-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}", "commit", "-q", "-m", message)
        sha = self._git(cfg, "rev-parse", "HEAD").stdout.strip()
        if cfg["work"] == cfg["branch"]:
            raise PluginError("the labdex branch can't be the Flux branch")
        # only ever labdex's own branch; the live branch changes when someone merges the request
        self._git(cfg, "push", "-q", "--force", "origin", f"{sha}:refs/heads/{cfg['work']}")
        url = ""
        if cfg["url"].startswith("https://"):
            from labdex import gitsource
            try:
                url = gitsource.open_merge_request({"url": cfg["url"], "branch": cfg["branch"]}, cfg["work"], message, token=cfg["token"],
                                                   body=f"Flux apps of cluster {self.cluster}, proposed by labdex. Merging it lets Flux apply them.")
            except gitsource.GitError as exc:
                raise PluginError(f"pushed {cfg['work']} but couldn't open the merge request: {exc}") from None
        self.log(f"pushed {sha[:10]} to {cfg['work']}" + (f"; merge request {url}" if url else ""))
        return {"commit": sha, "branch": cfg["work"], "merge_request": url, "files": len(changes)}
