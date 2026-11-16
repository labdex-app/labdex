"""Infrastructure as code: labdex drives Pulumi through its Automation API.

The inventory is the source of truth. A plugin turns the hosts of a site into a list of
`Resource`s (a Contabo instance per node, a Cloudflare record per name …); `engine` makes
an inline Pulumi program of them, previews it (a labdex plan, reviewed and approved like any
other) and applies it. State lives in labdex's data folder (Pulumi's file backend), one stack per
integration, encrypted with a passphrase from the integration's settings: no Pulumi Cloud.

Nothing here imports Pulumi at module level: labdex runs without it, and only an integration
that uses the engine needs `pip install labdex[pulumi]` and the `pulumi` CLI.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Resource:
    """One thing Pulumi should keep in existence.

    type:      what makes it, a key of the registry in engine.FACTORIES ("cloudflare:dns", "contabo:instance")
    name:      the logical name, unique in the stack and stable (a host's name, a record's FQDN)
    props:     inputs; values may be Ref(...) to another resource's output
    host:      the inventory host it belongs to ('' = none)
    import_id: adopt an existing resource with this provider id instead of creating one
    protect:   Pulumi refuses to delete it (a VPS that holds data)
    """

    type: str
    name: str
    props: dict = field(default_factory=dict)
    host: str = ""
    import_id: str = ""
    protect: bool = False
    depends_on: list = field(default_factory=list)  # names of other resources

    def as_dict(self) -> dict:
        return {"type": self.type, "name": self.name, "props": {k: (v.as_dict() if isinstance(v, Ref) else v) for k, v in self.props.items()},
                "host": self.host, "import_id": self.import_id, "protect": self.protect, "depends_on": list(self.depends_on)}


@dataclass(frozen=True)
class Ref:
    """An output of another resource of the same program: Ref("srv-vm-oak-01", "ip_v4")."""

    resource: str
    output: str
    fallback: str = ""  # used while the other resource isn't managed (no output yet)

    def as_dict(self) -> dict:
        return {"$ref": f"{self.resource}.{self.output}"}


class EngineError(Exception):
    """An expected failure of the engine (missing CLI, bad credentials); shown to the user."""


def check_unique(resources: list[Resource]) -> list[str]:
    """Names that appear more than once (Pulumi would refuse the program)."""
    seen: set = set()
    dup: list = []
    for r in resources:
        key = (r.type, r.name)
        if key in seen:
            dup.append(r.name)
        seen.add(key)
    return dup
