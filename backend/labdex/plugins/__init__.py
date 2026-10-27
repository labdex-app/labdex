"""Plugin registry: finds plugin folders, validates manifests, loads Python code,
and handles instance settings (with encrypted secrets).

Official plugins ship in labdex/official_plugins/ and update on their own from the official
repository; labdex/builtin_plugins/ only holds the demo lab. Your own live in LABDEX_PLUGIN_DIR
(default /data/plugins). Plugin code runs inside labdex with full access to
the inventory: only install plugins you trust.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
SETTING_TYPES = {"string", "secret", "bool", "int", "select", "text", "networks", "list", "table", "yaml"}
COLUMN_TYPES = {"string", "int", "bool", "select"}
MODES = {"run", "plan", "export"}
BUILTIN_DIR = Path(__file__).parent.parent / "builtin_plugins"
# official plugins that ship with labdex but update on their own from the official repository:
# an installed newer copy in the plugin folder takes their place
BUNDLED_DIR = Path(__file__).parent.parent / "official_plugins"


def compatibility(m: dict) -> tuple[bool, str]:
    """A plugin says which labdex it works with: `labdex: ">=1.0"` (any PEP 440 range, e.g.
    ">=1.0,<2"). No range: assumed to work. Within 1.x the plugin API only grows."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import Version

    from .. import __version__
    req = m.get("labdex") if isinstance(m, dict) else None
    if req in (None, ""):
        return True, ""
    try:
        rng = SpecifierSet(str(req))
    except InvalidSpecifier:
        return False, f"labdex: '{req}' isn't a version range like '>=1.0'"
    if rng.contains(Version(__version__), prereleases=True):
        return True, ""
    return False, f"needs labdex {req}, this is {__version__}"


@dataclass
class Spec:
    id: str
    path: Path
    builtin: bool
    bundled: bool = False  # ships with labdex in official_plugins/ (and can be updated from the official repository)
    replaces_bundled: str = ""  # an installed copy of a bundled plugin: the bundled version it stands in for
    isolated: bool = False  # its code runs in a locked-down process of its own (plugins/sandbox.py)
    manifest: dict = field(default_factory=dict)
    module: object = None
    error: str = ""
    trust: dict = field(default_factory=lambda: {"status": "unsigned", "signer": "", "key_id": "", "detail": ""})

    @property
    def name(self) -> str:
        return self.manifest.get("name", self.id)

    @property
    def kind(self) -> str:
        return self.manifest.get("kind", "integration")

    @property
    def settings(self) -> list[dict]:
        fields = list(self.manifest.get("settings") or [])
        for a in self.actions:
            if a.get("schedulable"):
                fields.append({"key": f"schedule_{a['id']}", "label": f"Run “{a['label']}” every … minutes",
                               "type": "int", "default": int(a.get("schedule_default") or 0), "help": "0 = only manually or by hooks",
                               "group": "Schedule"})
        return fields

    @property
    def actions(self) -> list[dict]:
        return list(self.manifest.get("actions") or [])

    def action(self, action_id: str) -> dict | None:
        return next((a for a in self.actions if a["id"] == action_id), None)

    @property
    def hooks(self) -> list[dict]:
        return list(self.manifest.get("hooks") or [])

    @property
    def endpoints(self) -> list[dict]:
        return list(self.manifest.get("endpoints") or [])

    @property
    def provides(self) -> list[str]:
        """Built-in pages this plugin feeds: discovery, features."""
        return list(self.manifest.get("provides") or [])

    @property
    def pages(self) -> list[dict]:
        """Extra pages in the sidebar: [{id, label}] rendered from page_<id>()."""
        return list(self.manifest.get("pages") or [])

    @property
    def host_settings(self) -> list[dict]:
        return list(self.manifest.get("host_settings") or [])

    @property
    def host_actions(self) -> list[dict]:
        return [a for a in self.actions if a.get("scope") == "host"]

    @property
    def host_view(self) -> bool:
        return bool(self.manifest.get("host_view"))

    @property
    def per_host(self) -> bool:
        """Does this plugin do anything per host (and so care about selectors)?"""
        return bool(self.host_settings or self.host_actions or self.host_view or self.manifest.get("per_host"))

    @property
    def widgets(self) -> list[dict]:
        return [w for w in self.manifest.get("widgets") or [] if isinstance(w, dict)]

    @property
    def capabilities(self) -> list[str]:
        return capabilities_of(self.manifest)

    def plugin_class(self):
        return getattr(self.module, "Plugin", None) if self.module else None

    def as_dict(self) -> dict:
        m = self.manifest
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "version": m.get("version", ""),
            "description": m.get("description", ""), "author": m.get("author", ""),
            "builtin": self.builtin, "bundled": self.bundled, "replaces_bundled": self.replaces_bundled, "isolated": self.isolated,
            "error": self.error, "settings": self.settings,
            "actions": [{k: a.get(k) for k in ("id", "label", "mode", "description", "schedulable", "danger", "scope")}
                        | {"ansible": bool(a.get("ansible")), "params": a.get("params") or []} for a in self.actions],
            "hooks": self.hooks, "endpoints": self.endpoints,
            "host_settings": self.host_settings, "host_view": self.host_view, "per_host": self.per_host,
            "trust": self.trust, "tags": list(m.get("tags") or []), "supported": bool(m.get("supported")),
            "homepage": m.get("homepage", ""), "provides": self.provides, "pages": self.pages,
            "capabilities": self.capabilities, "widgets": self.widgets,
            "requires": str(m.get("labdex") or ""),
            "path": str(self.path),
            "python": self.module is not None,
        }


CAPABILITIES = {
    "actions": "runs actions",
    "plans": "plans changes you review and apply",
    "exports": "makes files to download",
    "hooks": "reacts to inventory changes",
    "pages": "adds pages",
    "host": "works per host (settings, actions, a view)",
    "endpoints": "serves data to other tools",
    "widgets": "shows widgets on the dashboard and host pages",
    "ansible": "runs Ansible",
}


def capabilities_of(m: dict) -> list[str]:
    """What a plugin brings, read from its manifest (no code runs)."""
    acts = [a for a in m.get("actions") or [] if isinstance(a, dict)]
    have = {
        "actions": any(a.get("mode", "run") == "run" for a in acts),
        "plans": any(a.get("mode") == "plan" for a in acts),
        "exports": any(a.get("mode") == "export" for a in acts),
        "hooks": bool(m.get("hooks")),
        "pages": bool(m.get("pages") or m.get("provides")),
        "host": bool(m.get("host_settings") or m.get("host_view") or m.get("per_host") or any(a.get("scope") == "host" for a in acts)),
        "endpoints": bool(m.get("endpoints")),
        "widgets": bool(m.get("widgets")),
        "ansible": any(a.get("ansible") for a in acts),
    }
    return [k for k in CAPABILITIES if have[k]]


def _validate_fields(fields, where: str) -> str:
    keys = set()
    for s in fields or []:
        if not isinstance(s, dict) or not re.match(r"^[a-z][a-z0-9_]*$", str(s.get("key", ""))):
            return f"{where}: bad key {s!r}"
        if s.get("type", "string") not in SETTING_TYPES:
            return f"{where}.{s['key']}: unknown type {s.get('type')}"
        if s["key"] in keys:
            return f"{where}.{s['key']}: duplicate"
        if s.get("type") == "table":
            cols = s.get("columns") or []
            if not cols or any(not re.match(r"^[a-z][a-z0-9_]*$", str(c.get("key", ""))) or c.get("type", "string") not in COLUMN_TYPES for c in cols):
                return f"{where}.{s['key']}: a table needs columns with key and type ({', '.join(sorted(COLUMN_TYPES))})"
        when = s.get("when")
        if when is not None and (not isinstance(when, dict) or any(k not in keys for k in when)):
            return f"{where}.{s['key']}: when names settings above it, like when: {{auth: [password]}}"
        keys.add(s["key"])
    return ""


def _validate(m: dict, path: Path) -> str:
    if not isinstance(m, dict):
        return "plugin.yaml must be a mapping"
    if not ID_RE.match(str(m.get("id", ""))):
        return "id: lowercase letters, digits and dashes"
    if m.get("labdex") not in (None, ""):
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        try:
            SpecifierSet(str(m["labdex"]))
        except InvalidSpecifier:
            return f"labdex: '{m['labdex']}' isn't a version range like '>=1.0'"
    if m.get("kind", "integration") not in ("integration", "module"):
        return "kind: integration or module"
    err = _validate_fields(m.get("settings"), "settings") or _validate_fields(m.get("host_settings"), "host_settings")
    if err:
        return err
    ids = set()
    for a in m.get("actions") or []:
        if not isinstance(a, dict) or not re.match(r"^[a-z][a-z0-9_]*$", str(a.get("id", ""))):
            return f"actions: bad id {a!r}"
        if a.get("scope", "global") not in ("global", "host"):
            return f"actions.{a['id']}: scope must be global or host"
        err = _validate_fields(a.get("params"), f"actions.{a['id']}.params")
        if err:
            return err
        if a.get("mode", "run") not in MODES:
            return f"actions.{a['id']}: mode must be run, plan or export"
        if a.get("ansible") and not (path / a["ansible"]).is_file():
            return f"actions.{a['id']}: playbook {a['ansible']} not found"
        if a["id"] in ids:
            return f"actions.{a['id']}: duplicate"
        ids.add(a["id"])
        a.setdefault("mode", "run")
        a.setdefault("label", a["id"].replace("_", " ").capitalize())
    for h in m.get("hooks") or []:
        if h.get("action") not in ids:
            return f"hooks: unknown action {h.get('action')!r}"
    wids = set()
    for w in m.get("widgets") or []:
        if not isinstance(w, dict) or not re.match(r"^[a-z][a-z0-9_]*$", str(w.get("id", ""))):
            return f"widgets: bad id {w!r}"
        if w.get("where", "dashboard") not in ("dashboard", "host"):
            return f"widgets.{w['id']}: where must be dashboard or host"
        if w.get("size", "small") not in ("small", "wide"):
            return f"widgets.{w['id']}: size must be small or wide"
        if w["id"] in wids:
            return f"widgets.{w['id']}: duplicate"
        wids.add(w["id"])
    return ""


def _load_dir(path: Path, builtin: bool, community: dict | None = None, bundled: bool = False,
              sandbox: str = "on") -> Spec | None:
    mf = path / "plugin.yaml"
    if not mf.is_file():
        return None
    spec = Spec(id=path.name, path=path, builtin=builtin, bundled=bundled)
    try:
        spec.manifest = yaml.safe_load(mf.read_text()) or {}
    except yaml.YAMLError as exc:
        spec.error = f"plugin.yaml: {exc}"
        return spec
    spec.id = str(spec.manifest.get("id", path.name))
    spec.error = _validate(spec.manifest, path)
    if spec.error:
        return spec
    if builtin:
        spec.trust = {"status": "builtin", "signer": "labdex", "key_id": "", "detail": "ships with labdex"}
    elif bundled:
        spec.trust = {"status": "official", "signer": "labdex", "key_id": "",
                      "detail": "ships with labdex; updates come from the official repository"}
    else:
        ok, why = compatibility(spec.manifest)
        if not ok:
            spec.error = f"not loaded: {why}"
            return spec
        from .trust import verify
        try:
            spec.trust = verify(path, spec.manifest, community)
        except ValueError as exc:
            spec.trust = {"status": "invalid", "signer": "", "key_id": "", "detail": str(exc)}
        if spec.trust["status"] == "invalid":
            spec.error = f"not loaded: {spec.trust['detail']}"
            return spec  # never run code whose signature doesn't hold
    py = path / spec.manifest.get("python", "plugin.py")
    from . import sandbox as sb
    if sb.wanted(spec, sandbox):
        spec.isolated = True  # its code never runs inside labdex: read, don't import
        if py.is_file():
            try:
                spec.module = sb.stand_in(spec, py)
            except (SyntaxError, ValueError, OSError) as exc:
                spec.error = f"plugin.py: {type(exc).__name__}: {exc}"
        return spec
    if py.is_file():
        modname = f"labdex_plugin_{spec.id.replace('-', '_')}"
        try:
            sp = importlib.util.spec_from_file_location(modname, py)
            mod = importlib.util.module_from_spec(sp)
            sys.modules[modname] = mod
            sp.loader.exec_module(mod)
            spec.module = mod
        except Exception as exc:  # a broken plugin must not take labdex down
            spec.error = f"plugin.py: {type(exc).__name__}: {exc}"
    return spec


class Registry:
    def __init__(self, extra_dir: str | None, community: dict | None = None, demo: bool = False, sandbox: str = "on"):
        self.extra_dir = extra_dir
        self.sandbox = sandbox
        self.specs: dict[str, Spec] = {}
        self.errors: list[dict] = []
        dirs = [(BUILTIN_DIR, "builtin"), (BUNDLED_DIR, "bundled")]
        if extra_dir:
            dirs.append((Path(extra_dir), "installed"))
        for base, kind in dirs:
            if not base.is_dir():
                continue
            for sub in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
                spec = _load_dir(sub, kind == "builtin", community, bundled=kind == "bundled", sandbox=sandbox)
                if spec is None or (spec.manifest.get("demo_only") and not demo):
                    continue
                cur = self.specs.get(spec.id)
                if cur is not None and cur.bundled and kind == "installed":
                    if spec.error:  # a broken update never takes the working bundled copy's place
                        self.errors.append({"path": str(sub), "error": f"{spec.id}: {spec.error}; using the bundled version"})
                        continue
                    if spec.trust.get("status") != "official":  # nor one the project didn't sign
                        self.errors.append({"path": str(sub), "error": f"{spec.id}: not signed with the labdex project key; using the bundled version"})
                        continue
                    spec.replaces_bundled = str(cur.manifest.get("version", ""))
                    self.specs[spec.id] = spec
                    continue
                if cur is not None:
                    self.errors.append({"path": str(sub), "error": f"duplicate plugin id '{spec.id}'"})
                    continue
                self.specs[spec.id] = spec

    def get(self, plugin_id: str) -> Spec | None:
        return self.specs.get(plugin_id)


# --------------------------------------------------------------------- settings
def _coerce_cell(col: dict, raw):
    typ = col.get("type", "string")
    if typ == "bool":
        return raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
    if typ == "int":
        return int(raw) if raw not in (None, "") else None
    val = "" if raw is None else str(raw).strip()
    if typ == "select":
        opts = [o if isinstance(o, str) else o.get("value") for o in col.get("options") or []]
        if val and val not in opts:
            raise ValueError(f"{col['key']}: one of {', '.join(opts)}")
    return val


def shown(field: dict, values: dict) -> bool:
    """A field with `when: {auth: [password, classic]}` only counts while those match."""
    for k, want in (field.get("when") or {}).items():
        want = want if isinstance(want, list) else [want]
        if values.get(k) not in want:
            return False
    return True


def coerce_fields(fields: list[dict], incoming: dict, stored: dict, vault) -> tuple[dict, list[dict]]:
    """Validate form input against a schema. Empty secret = keep the stored one;
    {"$ref": "vault:path#field"} points a secret at the vault (a bare name: vault:global/<name>#value)."""
    out, errors = {}, []
    for f in fields:
        key, typ = f["key"], f.get("type", "string")
        raw = incoming.get(key, None)
        if not shown(f, out):
            if key in stored:
                out[key] = stored[key]  # kept for when it's shown again, never required while hidden
            continue
        if typ == "secret":
            if isinstance(raw, dict) and raw.get("$ref"):
                from .. import secretstore as secrets
                ref = str(raw["$ref"]).strip()
                was = stored.get(key)
                old = [was["$ref"]] if isinstance(was, dict) and was.get("$ref") else []
                bad = secrets.check_new(old, [ref], key)
                if bad:
                    errors += bad
                    continue
                out[key] = {"$ref": ref}
                continue
            if raw in (None, ""):
                if key in stored:
                    out[key] = stored[key]
                elif f.get("required"):
                    errors.append({"field": key, "message": "required"})
                continue
            if raw == "__clear__":
                if f.get("required"):
                    errors.append({"field": key, "message": "required"})
                continue
            out[key] = vault.encrypt(str(raw))
            continue
        if raw is None:
            raw = stored.get(key, f.get("default"))
        try:
            if typ == "bool":
                val = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
            elif typ == "int":
                val = int(raw) if raw not in (None, "") else (f.get("default") or 0)
                if f.get("min") is not None and val < f["min"] or f.get("max") is not None and val > f["max"]:
                    raise ValueError(f"between {f.get('min')} and {f.get('max')}")
            elif typ in ("networks", "list"):
                val = raw if isinstance(raw, list) else [x.strip() for x in str(raw or "").replace("\n", ",").split(",") if x.strip()]
                val = [str(x).strip() for x in val if str(x).strip()]
            elif typ == "select":
                val = raw if raw is not None else ""
                opts = [o if isinstance(o, str) else o.get("value") for o in f.get("options") or []]
                if val not in opts and (val or f.get("required")):
                    raise ValueError(f"one of {', '.join(opts)}")
            elif typ == "table":
                rows = raw or []
                if not isinstance(rows, list):
                    raise ValueError("a list of rows")
                val = []
                for n, row in enumerate(rows):
                    if not isinstance(row, dict):
                        raise ValueError(f"row {n + 1}: not a mapping")
                    clean = {c["key"]: _coerce_cell(c, row.get(c["key"])) for c in f["columns"]}
                    if all(v in ("", None, False) for v in clean.values()):
                        continue  # empty row from the form
                    missing = [c["key"] for c in f["columns"] if c.get("required") and clean[c["key"]] in ("", None)]
                    if missing:
                        raise ValueError(f"row {n + 1}: {', '.join(missing)} required")
                    val.append(clean)
            elif typ == "yaml":
                from ..features import parse_vars
                val = parse_vars(raw, key)
            else:
                val = "" if raw is None else str(raw)
        except (TypeError, ValueError) as exc:
            errors.append({"field": key, "message": str(exc) or "invalid value"})
            continue
        except Exception as exc:  # store.ValidationError from parse_vars
            errors.append({"field": key, "message": getattr(exc, "errors", [{"message": str(exc)}])[0]["message"]})
            continue
        if f.get("required") and val in ("", None, []):
            errors.append({"field": key, "message": "required"})
        out[key] = val
    return out, errors


def coerce_settings(spec: Spec, incoming: dict, stored: dict, vault) -> tuple[dict, list[dict]]:
    return coerce_fields(spec.settings, incoming, stored, vault)


def public_fields(fields: list[dict], stored: dict) -> tuple[dict, list[str], dict]:
    """Values for the UI: secrets are never sent back, only whether they're set
    (and which global secret they point at)."""
    values, secrets_set, refs = {}, [], {}
    for f in fields:
        key = f["key"]
        if f.get("type") == "secret":
            values[key] = ""
            if key in stored:
                secrets_set.append(key)
                if isinstance(stored[key], dict) and stored[key].get("$ref"):
                    refs[key] = stored[key]["$ref"]
        else:
            empty = {"bool": None, "int": None, "networks": [], "list": [], "table": [], "yaml": {}}.get(f.get("type"), "")
            values[key] = stored.get(key, f.get("default", empty))
    return values, secrets_set, refs


def public_settings(spec: Spec, stored: dict) -> tuple[dict, list[str]]:
    values, secrets_set, _ = public_fields(spec.settings, stored)
    return values, secrets_set


def resolve_secret(stored, vault) -> str:
    if isinstance(stored, dict) and stored.get("$ref"):
        from .. import secretstore as secrets
        from .base import PluginError
        try:
            val = secrets.reveal(stored["$ref"])
        except secrets.RefError as exc:
            raise PluginError(str(exc)) from None
        if val is None:
            raise PluginError(f"secret {secrets.label(stored['$ref'])} isn't in the vault any more")
        return val
    return vault.decrypt(stored)


def resolved_fields(fields: list[dict], stored: dict, vault) -> dict:
    out = {}
    for f in fields:
        key = f["key"]
        if f.get("type") == "secret":
            out[key] = resolve_secret(stored[key], vault) if key in stored else ""
        else:
            out[key] = stored.get(key, f.get("default"))
    return out


def resolved_settings(spec: Spec, stored: dict, vault) -> dict:
    return resolved_fields(spec.settings, stored, vault)
