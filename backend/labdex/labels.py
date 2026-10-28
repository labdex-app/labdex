"""Printable labels as SVG: a host (name, fun name, addresses, zone) or a cable (its number and both
ends), each with a QR code that opens it in labdex. Sized in millimetres, so they print true on a
label printer; the shape is a landscape strip, 62 × 29 mm by default."""
from __future__ import annotations

from xml.sax.saxutils import escape

import segno

W, H = 62, 29  # mm
SIZES = {"62x29": (62, 29), "50x25": (50, 25), "89x36": (89, 36)}


def parse_size(text: str | None) -> tuple[int, int]:
    return SIZES.get(text or "62x29", (W, H))


def qr_path(url: str) -> tuple[int, str]:
    """(modules per side, SVG path of the dark modules) for a URL; error correction M."""
    code = segno.make(url, error="m", micro=False)
    d = []
    for y, row in enumerate(code.matrix):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                d.append(f"M{start} {y}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return len(code.matrix), "".join(d)


def _fit(text: str, size: float, width: float) -> float:
    """Font size that fits the text in width (monospace-ish estimate: 0.6 em per character)."""
    return min(size, width / max(len(text), 1) / 0.6)


def _svg(w: int, h: int, url: str, lines: list[tuple[str, float, str]]) -> str:
    """lines: (text, font size in mm, css class) from the top; the QR sits on the left."""
    n, path = qr_path(url)
    qr = h - 6  # mm, with a 3 mm margin around it
    scale = qr / n
    tx = qr + 7
    avail = w - tx - 2
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}" role="img">',
           f'<rect width="{w}" height="{h}" fill="#fff"/>',
           f'<g transform="translate(3 3) scale({scale:.4f})"><path d="{path}" fill="#000" shape-rendering="crispEdges"/></g>']
    y = 3.0
    for text, size, weight in lines:
        if not text:
            continue
        fs = _fit(text, size, avail)
        y += fs
        out.append(f'<text x="{tx}" y="{y:.2f}" font-family="DejaVu Sans Mono, Menlo, Consolas, monospace" font-size="{fs:.2f}" '
                   f'font-weight="{weight}" fill="#000">{escape(text)}</text>')
        y += fs * 0.35
    out.append("</svg>")
    return "".join(out)


def host_label(host: dict, url: str, size: tuple[int, int] = (W, H)) -> str:
    """host: store.host_to_dict. Fun name big when there is one, then the name, addresses and zone."""
    w, h = size
    ips = [i["ip4"] or (i.get("ip6_all") or [""])[0] for i in host.get("interfaces", []) if i.get("ip4") or i.get("ip6_all")]
    first = host.get("fun") or host["name"]
    lines = [(first, 5.2, "700")]
    if host.get("fun"):
        lines.append((host["name"], 3.2, "400"))
    for ip in ips[:2]:
        lines.append((ip, 3.2, "400"))
    lines.append((host.get("zone", ""), 3.0, "700"))
    return _svg(w, h, url, lines)


def cable_label(cable: dict, a: tuple[str, str], b: tuple[str, str], url: str, size: tuple[int, int] = (W, H)) -> str:
    """cable: {label, medium, speed, length}; a, b: (host, port). Print one at each end."""
    w, h = size
    meta = " ".join(x for x in (cable.get("medium"), cable.get("speed"), f"{cable['length']} m" if cable.get("length") else "") if x)
    lines = [(cable.get("label") or "cable", 5.2, "700"), (f"{a[0]} {a[1]}", 3.0, "400"), (f"{b[0]} {b[1]}", 3.0, "400"), (meta, 2.8, "400")]
    return _svg(w, h, url, lines)
