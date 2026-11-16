"""Run a list of `Resource`s as an inline Pulumi program, through the Automation API.

    eng = Engine(project="labdex-public", stack="public", state_dir=…, passphrase=…, env={…})
    steps = eng.preview(resources, log)      # [Step]: what would change (nothing is touched)
    result = eng.up(resources, log)          # apply; {"summary": {...}, "outputs": {...}}

Pulumi itself (the `pulumi` package and CLI) is only needed here; `available()` says whether it's
there. Plans map preview steps to labdex Changes in `steps_to_changes`, which the tests use
without Pulumi.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import EngineError, Ref, Resource, check_unique

# preview operations that change something, and how labdex shows them
OPS = {"create": "create", "update": "update", "delete": "delete", "replace": "replace", "create-replacement": "replace",
       "import": "import", "import-replacement": "import", "delete-replaced": None, "same": None, "read": None, "refresh": None,
       "discard": None, "discard-replaced": None, "remove-pending-replace": None, "read-replacement": None}


@dataclass
class Step:
    op: str  # create, update, delete, replace, import
    type: str  # the resource type token (cloudflare:index/dnsRecord:DnsRecord …)
    name: str  # the logical name
    urn: str = ""
    diffs: list = field(default_factory=list)  # changed input keys
    old: dict | None = None
    new: dict | None = None


def available() -> tuple[bool, str]:
    """(True, '') when previews and updates can run here; else why not."""
    try:
        import pulumi  # noqa: F401
        from pulumi import automation  # noqa: F401
    except ImportError:
        return False, "the Pulumi SDK isn't installed: pip install 'labdex[pulumi]' (or build the image with PULUMI=1)"
    if not shutil.which("pulumi") and not os.environ.get("LABDEX_PULUMI_CLI"):
        return False, "the pulumi CLI isn't on the PATH (build the image with PULUMI=1, or install it from pulumi.com)"
    return True, ""


def short_type(token: str) -> str:
    """cloudflare:index/dnsRecord:DnsRecord → cloudflare DnsRecord; pulumi-python:dynamic:Resource → dynamic."""
    if token.startswith("pulumi-python:dynamic"):
        return "contabo instance"
    pkg, _, rest = token.partition(":")
    return f"{pkg} {rest.rsplit(':', 1)[-1]}" if rest else token


def steps_to_changes(steps: list[Step]) -> list[dict]:
    """labdex Change dicts (op, kind, name, summary, before, after, data) for a plan."""
    out = []
    for s in steps:
        op = OPS.get(s.op, "other")
        if op is None:
            continue
        kind = short_type(s.type)
        what = {"create": "new", "update": "changes " + ", ".join(s.diffs) if s.diffs else "changes",
                "delete": "removed from the inventory", "replace": "replaced (a new one is made, then the old one goes)",
                "import": "adopted: Pulumi takes over the existing one"}.get(op, s.op)
        before = _public(s.old)
        after = _public(s.new)
        out.append({"op": {"replace": "update", "import": "other"}.get(op, op),
                    "kind": kind, "name": s.name, "summary": f"{kind} {s.name}: {what}", "before": before, "after": after,
                    "data": {"op": s.op, "urn": s.urn, "type": s.type}})
    return out


def _public(state: dict | None):
    """The inputs of a state, without secrets (Pulumi marks them) and credentials."""
    if not state:
        return None
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else state
    out = {}
    for k, v in (inputs or {}).items():
        if k.startswith("__") or any(w in k.lower() for w in ("password", "secret", "token", "key")):
            continue
        if isinstance(v, dict) and v.get("4dabf18193072939515e22adb298388d") is not None:  # Pulumi's secret signature
            v = "[secret]"
        out[k] = v
    return out


class Engine:
    def __init__(self, *, project: str, stack: str, state_dir: Path, passphrase: str, env: dict | None = None,
                 plugins: dict | None = None):
        if not passphrase:
            raise EngineError("set a state passphrase: Pulumi encrypts the stack's secrets with it")
        self.project = project
        self.stack_name = stack
        self.state_dir = Path(state_dir)
        self.passphrase = passphrase
        self.env = {k: v for k, v in (env or {}).items() if v}
        self.plugins = plugins or {}  # provider plugins to install first: {"cloudflare": "v6.4.0"}

    # ------------------------------------------------------------------ run
    def _stack(self, resources: list[Resource]):
        ok, why = available()
        if not ok:
            raise EngineError(why)
        dup = check_unique(resources)
        if dup:
            raise EngineError(f"two resources with the same name: {', '.join(sorted(set(dup)))}")
        from pulumi import automation as auto
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        env = {"PULUMI_CONFIG_PASSPHRASE": self.passphrase, "PULUMI_SKIP_UPDATE_CHECK": "true",
               "PULUMI_HOME": str(self.state_dir / "home"), **self.env}
        opts = auto.LocalWorkspaceOptions(
            work_dir=str(self.state_dir / "work"),
            env_vars=env,
            project_settings=auto.ProjectSettings(name=self.project, runtime="python",
                                                  backend=auto.ProjectBackend(url=f"file://{self.state_dir}")),
            secrets_provider="passphrase",
        )
        if os.environ.get("LABDEX_PULUMI_CLI"):
            opts.pulumi_command = auto.PulumiCommand(root=os.environ["LABDEX_PULUMI_CLI"], skip_version_check=True)
        (self.state_dir / "work").mkdir(exist_ok=True)
        stack = auto.create_or_select_stack(stack_name=self.stack_name, project_name=self.project, program=program(resources), opts=opts)
        for name, version in self.plugins.items():
            stack.workspace.install_plugin(name, version)
        return stack

    def preview(self, resources: list[Resource], log=print) -> list[Step]:
        stack = self._stack(resources)
        steps: list[Step] = []

        def on_event(ev):
            pre = getattr(ev, "resource_pre_event", None)
            if pre is None:
                return
            m = pre.metadata
            op = getattr(m.op, "value", str(m.op))
            if m.type == "pulumi:pulumi:Stack" or OPS.get(op, "other") is None:
                return
            name = m.urn.rsplit("::", 1)[-1]
            steps.append(Step(op, m.type, name, m.urn, list(m.diffs or []), _state(m.old), _state(m.new)))

        try:
            stack.preview(on_event=on_event, on_output=lambda line: log(line.rstrip()), diff=True)
        except Exception as exc:  # the CLI's error, shown in the job log
            raise EngineError(f"pulumi preview failed: {_short(exc)}") from exc
        return steps

    def up(self, resources: list[Resource], log=print) -> dict:
        stack = self._stack(resources)
        try:
            res = stack.up(on_output=lambda line: log(line.rstrip()))
        except Exception as exc:
            raise EngineError(f"pulumi up failed: {_short(exc)}") from exc
        return {"summary": dict(res.summary.resource_changes or {}), "outputs": {k: (None if v.secret else v.value) for k, v in res.outputs.items()}}

    def refresh(self, resources: list[Resource], log=print) -> dict:
        """Read every resource back from the providers into the state (drift shows in the next plan)."""
        stack = self._stack(resources)
        try:
            res = stack.refresh(on_output=lambda line: log(line.rstrip()))
        except Exception as exc:
            raise EngineError(f"pulumi refresh failed: {_short(exc)}") from exc
        return {"summary": dict(res.summary.resource_changes or {})}

    def outputs(self, resources: list[Resource]) -> dict:
        stack = self._stack(resources)
        return {k: (None if v.secret else v.value) for k, v in stack.outputs().items()}


def _state(st) -> dict | None:
    if st is None:
        return None
    return {"inputs": dict(getattr(st, "inputs", None) or {}), "outputs": dict(getattr(st, "outputs", None) or {})}


def _short(exc: Exception) -> str:
    text = str(exc)
    for marker in ("stderr:", "error:"):
        if marker in text:
            text = text[text.index(marker):]
            break
    return text.strip()[:1500]


# ------------------------------------------------------------------ program
def program(resources: list[Resource]):
    """The inline Pulumi program: makes each resource, in order, wiring Refs to outputs."""

    def run():
        import pulumi
        made: dict = {}
        for r in resources:
            factory = FACTORIES.get(r.type)
            if factory is None:
                raise EngineError(f"unknown resource type {r.type}")
            props = {k: _resolve(v, made) for k, v in r.props.items()}
            opts = pulumi.ResourceOptions(protect=r.protect, import_=r.import_id or None,
                                          depends_on=[made[d] for d in r.depends_on if d in made] or None)
            made[r.name] = factory(r.name, props, opts)
            for out in OUTPUTS.get(r.type, ()):
                pulumi.export(f"{r.name}.{out}", getattr(made[r.name], out))
    return run


def _resolve(v, made: dict):
    if isinstance(v, Ref):
        if v.resource in made:
            return getattr(made[v.resource], v.output)
        return v.fallback
    return v


def _cloudflare_dns(name: str, props: dict, opts):
    import pulumi_cloudflare as cloudflare
    return cloudflare.DnsRecord(name, zone_id=props["zone_id"], name=props["name"], type=props["type"], content=props["content"],
                                ttl=int(props.get("ttl") or 1), proxied=bool(props.get("proxied")), comment=props.get("comment") or None,
                                opts=opts)


def _contabo_instance(name: str, props: dict, opts):
    from .contabo_provider import ContaboInstance
    return ContaboInstance(name, props, opts)


FACTORIES = {"cloudflare:dns": _cloudflare_dns, "contabo:instance": _contabo_instance}
OUTPUTS = {"contabo:instance": ("instance_id", "ip_v4", "ip_v6"), "cloudflare:dns": ("id",)}
