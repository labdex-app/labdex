"""From the hosts of a site to the resources Pulumi keeps: a Contabo instance per node and a
Cloudflare record per name. Pure functions of the inventory and the settings, so a plan is the
same every time for the same inventory, and the tests need neither Pulumi nor the providers."""
from __future__ import annotations

import ipaddress

from . import Ref, Resource

# addresses that never go into public DNS (documentation ranges are fine: examples use them)
INTERNAL = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
                                              "169.254.0.0/16", "fc00::/7", "fe80::/10", "::1/128")]


def _public_ip(ip) -> bool:
    return not any(ip in n for n in INTERNAL if n.version == ip.version)


def _render(template: str, host: dict) -> str:
    fun = host.get("fun") or host["name"]
    return (template or "{fun}").replace("{fun}", fun).replace("{host}", host["name"]).strip(".").lower()


def _primary(host: dict, family: int) -> str:
    ifaces = sorted(host.get("interfaces") or [], key=lambda i: not i.get("primary"))
    for i in ifaces:
        for a in ([i.get("ip4")] if family == 4 else [i.get("ip6"), *(i.get("ip6_all") or [])]):
            if not a:
                continue
            try:
                ip = ipaddress.ip_address(a)
            except ValueError:
                continue
            if ip.version == family and _public_ip(ip):
                return str(ip)
    return ""


def resources(hosts: list[dict], settings: dict) -> tuple[list[Resource], list[str]]:
    """(resources, notes). `hosts` carry `module_settings` (this integration's host settings).

    settings: contabo (bool), cloudflare (bool), cloudflare_zone_id, cloudflare_zone (example.com),
    record_name ("{fun}.node"), record_ttl, proxied, ipv6, protect_instances, allow_cancel,
    default_product_id, default_region, default_image_id, ssh_keys
    """
    out: list[Resource] = []
    notes: list[str] = []
    zone = str(settings.get("cloudflare_zone") or "").strip(".").lower()
    for h in sorted(hosts, key=lambda x: x["name"]):
        hs = h.get("module_settings") or {}
        if h.get("kind") == "vip" or hs.get("skip"):
            continue
        instance = None
        if settings.get("contabo") and not hs.get("no_instance"):
            product = str(hs.get("product_id") or settings.get("default_product_id") or "")
            image = str(hs.get("image_id") or settings.get("default_image_id") or "")
            existing = str(hs.get("instance_id") or "")
            if not existing and not (product and image):
                notes.append(f"{h['name']}: no Contabo instance id, product or image; skipped")
            else:
                props = {"display_name": h["name"], "region": str(hs.get("region") or settings.get("default_region") or "EU"),
                         "allow_cancel": bool(settings.get("allow_cancel"))}
                if product:
                    props["product_id"] = product
                if image and not existing:  # an adopted instance keeps the image it runs
                    props["image_id"] = image
                keys = [k for k in str(settings.get("ssh_keys") or "").replace(",", " ").split() if k.isdigit()]
                if keys and not existing:
                    props["ssh_keys"] = keys
                instance = Resource("contabo:instance", h["name"], props, h["name"], existing,
                                    protect=bool(settings.get("protect_instances", True)))
                out.append(instance)
        if settings.get("cloudflare") and zone and settings.get("cloudflare_zone_id"):
            label = _render(str(hs.get("record_name") or settings.get("record_name") or "{fun}"), h)
            fqdn = f"{label}.{zone}" if label else zone
            for family, rtype in ((4, "A"), (6, "AAAA")):
                if family == 6 and not settings.get("ipv6"):
                    continue
                ip = _primary(h, family)
                content: object = ip
                if not ip and instance is not None:
                    content = Ref(h["name"], "ip_v4" if family == 4 else "ip_v6")
                if not content:
                    if family == 4:
                        notes.append(f"{h['name']}: no public IPv{family} address; no {rtype} record")
                    continue
                out.append(Resource("cloudflare:dns", f"{fqdn}/{rtype}",
                                    {"zone_id": settings["cloudflare_zone_id"], "name": fqdn, "type": rtype, "content": content,
                                     "ttl": int(settings.get("record_ttl") or 1), "proxied": bool(hs.get("proxied", settings.get("proxied", False))),
                                     "comment": "labdex"},
                                    h["name"], f"{settings['cloudflare_zone_id']}/{rid}" if (rid := str(hs.get(f"record_id_{rtype.lower()}") or "")) else "",
                                    depends_on=[h["name"]] if isinstance(content, Ref) else []))
    return out, notes
