"""Make a large, realistic test inventory from examples/homelab.yaml.

    python tools/big_inventory.py 1000 > /tmp/big.yaml

Adds /24 lab networks in DC_TC and fills them with VMs (one interface, a
service, tags, some with a fun name and a parent hypervisor). Deterministic,
so timings are comparable between runs. Used by the performance budget test.
"""
import sys
from pathlib import Path

import yaml

EXAMPLE = Path(__file__).parents[2] / "examples" / "homelab.yaml"
ROLES = ["web", "db", "app", "cache", "mq", "ci", "mon", "log", "git", "dns"]
FUN = ["abra", "bulbasaur", "charmander", "diglett", "eevee", "flareon", "geodude", "haunter", "ivysaur", "jigglypuff"]


def build(n: int) -> dict:
    inv = yaml.safe_load(EXAMPLE.read_text())
    hypervisors = [h["name"] for h in inv["hosts"] if "-pm-" in h["name"]] or [None]
    per_net = 200
    for k in range((n + per_net - 1) // per_net):
        vlan = 300 + k
        inv["networks"].append({"name": f"lab{k:02d}", "vlan": vlan, "zone": "DC_TC", "prefix4": f"10.20.{k}.0/24",
                                "gateway4": f"10.20.{k}.254", "notes": "generated"})
    for i in range(n):
        net, octet = divmod(i, per_net)
        role = f"{ROLES[i % len(ROLES)]}{i // len(ROLES):03d}"
        h = {
            "name": f"srv-vm-{role}-01", "zone": "DC_TC", "role": f"generated {ROLES[i % len(ROLES)]}",
            "status": "active" if i % 17 else "planned", "tags": ["generated", ROLES[i % len(ROLES)]],
            "interfaces": [{"network": f"lab{net:02d}", "ip4": f"10.20.{net}.{octet + 1}", "primary": True,
                            "mac": f"02:00:00:{net:02x}:{octet // 256:02x}:{octet % 256:02x}"}],
            "services": [{"name": ROLES[i % len(ROLES)], "port": 8000 + i % 100, "scheme": "http"}],
        }
        if i % 3 == 0:
            h["fun"] = f"{FUN[i % len(FUN)]}{i}"
        if hypervisors[0]:
            h["runs_on"] = hypervisors[i % len(hypervisors)]
        inv["hosts"].append(h)
    return inv


if __name__ == "__main__":
    print(yaml.safe_dump(build(int(sys.argv[1]) if len(sys.argv) > 1 else 1000), sort_keys=False))
