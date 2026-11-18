"""Flux files as code: a cluster's apps, written by labdex as Flux manifests in a git repository.

An app is a small dict (the Kubernetes plugin keeps them per cluster):

    {name: grafana, namespace: monitoring, helm: {repo: https://grafana.github.io/helm-charts, chart: grafana, version: "8.x"},
     values: {...}, depends_on: [infra], interval: 10m}
    {name: home-apps, path: ./apps/home, source: flux-system, prune: true}

render() turns them into files under one folder of the Flux repository (one file per app plus a
kustomization.yaml that lists them). Every file starts with MARK, and diff() only ever removes
files that carry it, so files written by people in the same folder are left alone.
"""
from __future__ import annotations

import difflib
import re

import yaml

MARK = "# Managed by labdex"
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
INTERVAL_RE = re.compile(r"^\d+(s|m|h)$")
KUSTOMIZE = "kustomize.toolkit.fluxcd.io/v1"
HELM = "helm.toolkit.fluxcd.io/v2"
SOURCE = "source.toolkit.fluxcd.io/v1"


class AppError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        self.errors = errors


def parse(text: str) -> list[dict]:
    """The apps as YAML (a list, or {apps: [...]}) → validated apps."""
    try:
        data = yaml.safe_load(text or "") or []
    except yaml.YAMLError as exc:
        raise AppError([{"field": "apps", "message": f"not YAML: {str(exc).splitlines()[0]}"}]) from None
    if isinstance(data, dict):
        data = data.get("apps") or []
    if not isinstance(data, list):
        raise AppError([{"field": "apps", "message": "a list of apps"}])
    return validate(data)


def validate(apps: list) -> list[dict]:
    errors, out, seen = [], [], set()
    for i, a in enumerate(apps):
        f = f"apps[{i}]"
        if not isinstance(a, dict):
            errors.append({"field": f, "message": "an app: {name, path or helm, …}"})
            continue
        name = str(a.get("name") or "")
        if not NAME_RE.match(name):
            errors.append({"field": f"{f}.name", "message": "lower-case letters, digits and -, at most 63"})
        elif name in seen:
            errors.append({"field": f"{f}.name", "message": f"'{name}' twice"})
        seen.add(name)
        ns = str(a.get("namespace") or name)
        if not NAME_RE.match(ns):
            errors.append({"field": f"{f}.namespace", "message": "lower-case letters, digits and -"})
        interval = str(a.get("interval") or "10m")
        if not INTERVAL_RE.match(interval):
            errors.append({"field": f"{f}.interval", "message": "like 10m, 1h or 30s"})
        deps = [str(d) for d in a.get("depends_on") or []]
        app = {"name": name, "namespace": ns, "interval": interval, "depends_on": deps}
        helm, path = a.get("helm"), a.get("path")
        if bool(helm) == bool(path):
            errors.append({"field": f, "message": "either path (a folder in the Flux repository) or helm (repo, chart, version)"})
        elif path:
            p = str(path)
            if ".." in p.split("/") or p.startswith("/"):
                errors.append({"field": f"{f}.path", "message": "a relative folder in the repository, like ./apps/home"})
            app.update(kind="kustomization", path=p if p.startswith("./") else "./" + p, source=str(a.get("source") or "flux-system"),
                       prune=bool(a.get("prune", True)), target_namespace=str(a.get("target_namespace") or ""))
        else:
            if not isinstance(helm, dict) or not helm.get("repo") or not helm.get("chart"):
                errors.append({"field": f"{f}.helm", "message": "repo and chart (and a version)"})
            else:
                repo = str(helm["repo"])
                if not re.match(r"^(https://|oci://)\S+$", repo):
                    errors.append({"field": f"{f}.helm.repo", "message": "an https:// or oci:// address"})
                values = a.get("values") or {}
                if not isinstance(values, dict):
                    errors.append({"field": f"{f}.values", "message": "a mapping"})
                app.update(kind="helm", repo=repo, chart=str(helm["chart"]), version=str(helm.get("version") or "*"),
                           values=values if isinstance(values, dict) else {})
        out.append(app)
    names = {a["name"] for a in out}
    for i, a in enumerate(out):
        for d in a["depends_on"]:
            if d not in names:
                errors.append({"field": f"apps[{i}].depends_on", "message": f"no app '{d}'"})
    if errors:
        raise AppError(errors)
    return out


def _doc(objs: list[dict], header: str) -> str:
    return header + "".join("---\n" + yaml.safe_dump(o, sort_keys=False) for o in objs)


def _deps(a: dict, ns: str) -> list[dict]:
    return [{"name": d, "namespace": ns} for d in a["depends_on"]]


def app_objects(a: dict, flux_ns: str = "flux-system") -> list[dict]:
    meta = {"name": a["name"], "namespace": flux_ns, "labels": {"app.kubernetes.io/managed-by": "labdex"}}
    if a["kind"] == "kustomization":
        spec = {"interval": a["interval"], "path": a["path"], "prune": a["prune"],
                "sourceRef": {"kind": "GitRepository", "name": a["source"]}}
        if a["target_namespace"]:
            spec["targetNamespace"] = a["target_namespace"]
        if a["depends_on"]:
            spec["dependsOn"] = _deps(a, flux_ns)
        return [{"apiVersion": KUSTOMIZE, "kind": "Kustomization", "metadata": meta, "spec": spec}]
    oci = a["repo"].startswith("oci://")
    repo = {"apiVersion": SOURCE, "kind": "HelmRepository", "metadata": dict(meta),
            "spec": {"interval": "1h", "url": a["repo"], **({"type": "oci"} if oci else {})}}
    spec = {"interval": a["interval"], "targetNamespace": a["namespace"], "releaseName": a["name"],
            "install": {"createNamespace": True},
            "chart": {"spec": {"chart": a["chart"], "version": a["version"],
                               "sourceRef": {"kind": "HelmRepository", "name": a["name"], "namespace": flux_ns}}}}
    if a["depends_on"]:
        spec["dependsOn"] = _deps(a, flux_ns)
    if a["values"]:
        spec["values"] = a["values"]
    return [repo, {"apiVersion": HELM, "kind": "HelmRelease", "metadata": dict(meta), "spec": spec}]


def render(apps: list[dict], who: str, flux_ns: str = "flux-system") -> dict[str, str]:
    """filename → content, relative to the cluster's folder."""
    header = f"{MARK} ({who}). Edit the apps in labdex; changes made here are overwritten.\n"
    files = {f"{a['name']}.yaml": _doc(app_objects(a, flux_ns), header) for a in apps}
    files["kustomization.yaml"] = header + yaml.safe_dump(
        {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": sorted(f for f in files)}, sort_keys=False)
    return files


def managed(text: str | None) -> bool:
    return bool(text) and text.startswith(MARK)


def diff(want: dict[str, str], have: dict[str, str]) -> list[dict]:
    """[{op: create|update|delete|conflict, file, diff}]: what writing `want` over `have` changes.
    A file of the same name that labdex didn't write is a conflict, never overwritten."""
    out = []
    for name in sorted(set(want) | set(have)):
        new, old = want.get(name), have.get(name)
        if new == old:
            continue
        if old is not None and not managed(old):
            if new is not None:
                out.append({"op": "conflict", "file": name, "diff": "", "summary": "a file labdex didn't write has this name; rename the app or the file"})
            continue
        op = "create" if old is None else "delete" if new is None else "update"
        d = "".join(difflib.unified_diff((old or "").splitlines(True), (new or "").splitlines(True), f"a/{name}", f"b/{name}"))
        added = sum(1 for line in d.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in d.splitlines() if line.startswith("-") and not line.startswith("---"))
        out.append({"op": op, "file": name, "diff": d, "summary": f"+{added} −{removed} lines"})
    return out
