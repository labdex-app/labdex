"""The Pulumi side of Contabo instances (imports Pulumi: only loaded by the engine).

Module-level classes on purpose: Pulumi serialises a dynamic provider by reference, and the
provider process imports this module again to run it.
"""
from __future__ import annotations

from pulumi.dynamic import CreateResult, DiffResult, ReadResult, Resource, ResourceProvider, UpdateResult

from .contabo import Client, diff, outputs


class InstanceProvider(ResourceProvider):
    def create(self, props):
        row = Client.from_env().create(props)
        return CreateResult(str(row["instanceId"]), outputs(row, props))

    def read(self, id_, props):
        row = Client.from_env().instance(id_)
        if row is None:
            return ReadResult(None, {})  # gone at Contabo: Pulumi drops it from the state
        return ReadResult(str(id_), outputs(row, props or {}))

    def diff(self, id_, olds, news):
        changed, replaces = diff(olds, news)
        return DiffResult(changes=bool(changed), replaces=replaces, delete_before_replace=False)

    def update(self, id_, olds, news):
        c = Client.from_env()
        if news.get("display_name") and news["display_name"] != olds.get("display_name"):
            c.rename(id_, news["display_name"])
        return UpdateResult(outputs(c.instance(id_) or {}, news))

    def delete(self, id_, props):
        if props.get("allow_cancel"):
            Client.from_env().cancel(id_)
        # otherwise labdex only forgets it: the instance keeps running at Contabo


class ContaboInstance(Resource):
    instance_id: str
    ip_v4: str
    ip_v6: str
    status: str

    def __init__(self, name: str, props: dict, opts=None):
        outs = {"instance_id": None, "ip_v4": None, "ip_v6": None, "status": None}
        super().__init__(InstanceProvider(), name, {**outs, **props}, opts)
