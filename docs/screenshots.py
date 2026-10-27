#!/usr/bin/env python3
"""The screenshots in these docs, taken from a fresh demo instance.

    cd frontend && npm run build && cd ..
    python3 docs/screenshots.py            # writes docs/screenshots/*.png

It starts labdex in demo mode on a free port with a throwaway data folder, adds a little
(a patch panel and a cable run, a MAC table) so every page has something to show, and
photographs each page in the light theme at 1440×900. Needs Playwright with Chromium.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
H = {"X-Requested-With": "labdex"}

# (file, path, what to wait for, optional action in the page before the picture)
SHOTS = [
    ("dashboard", "/", "text=Last changes", "wait"),
    ("hosts", "/hosts", "table tbody tr", None),
    ("host", "/hosts/srv-vm-dns-01", ".tabs", "wait"),
    ("host-network", "/hosts/srv-vm-dns-01?tab=network", ".list", None),
    ("topology", "/topology", ".canvas canvas", "wait"),
    ("topology-l3", "/topology?view=l3", ".canvas canvas", "wait"),
    ("cables", "/cables", "table tbody tr", None),
    ("cable-trace", "/cables", "table tbody tr", "trace"),
    ("where-is", "/discovery?tab=where&q=srv-vm-dns-01", ".mres", None),
    ("policy", "/policy", ".matrix", None),
    ("plugins", "/plugins", ".pcard", None),
    ("marketplace", "/plugins?tab=market", ".mcard", None),
    ("history", "/history", "h1", None),
    ("racks", "/racks", "h1", None),
]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def seed(base: str, s: requests.Session) -> None:
    """A patch panel between a server and the switch, and the switch's MAC table."""
    ifaces = []
    for n in range(1, 13):
        ifaces += [{"label": str(n), "kind": "ethernet", "connector": "rj45", "passthrough": f"r{n}"}, {"label": f"r{n}", "kind": "ethernet", "connector": "rj45"}]
    s.post(f"{base}/api/hosts", json={"name": "net-pp-rack-01", "zone": "DC_TC", "function": "patch panel", "interfaces": ifaces}, headers=H)
    for a, ap, b, bp, extra in (("srv-pm-pve-01", "eno2", "net-pp-rack-01", "3", {"label": "C-0101", "colour": "blue", "length": "1"}),
                                ("net-pp-rack-01", "r3", "net-sw-core-01", "ge-0/0/12", {"label": "C-0102", "colour": "yellow", "length": "0.5"})):
        s.post(f"{base}/api/topology/cables", json={"a": {"host": a, "port": ap}, "b": {"host": b, "port": bp}, "medium": "copper", "speed": "1G", **extra}, headers=H)


def seed_macs(data: Path) -> None:
    code = ("from labdex import create_app, macs, store\nfrom labdex.models import db\napp=create_app()\n"
            "with app.app_context():\n h=store.find_host('srv-vm-dns-01')\n m=next((i.mac for i in h.interfaces if i.mac), '') or '00:1a:2b:00:00:53'\n if not h.interfaces[0].mac: h.interfaces[0].mac=m\n"
            " macs.record('snmp core', [{'host':'net-sw-core-01','port':'ge-0/0/2','mac':m,'vlan':30},{'host':'net-sw-core-01','port':'ge-0/0/24','mac':m,'vlan':30},"
            "{'host':'net-sw-core-01','port':'ge-0/0/24','mac':'aa:aa:aa:aa:aa:01'},{'host':'net-sw-core-01','port':'ge-0/0/24','mac':'aa:aa:aa:aa:aa:02'}])\n db.session.commit()\n")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT / "backend", env={**os.environ, "LABDEX_DATA": str(data), "LABDEX_DEMO": "1", "LABDEX_SCHEDULER": "0",
                                                     "LABDEX_DEV_PLUGINS": str(ROOT / "plugins")}, check=True)


def main() -> None:
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        port, data = free_port(), Path(tmp)
        env = {**os.environ, "LABDEX_DATA": str(data), "LABDEX_DEMO": "1", "LABDEX_SCHEDULER": "0", "LABDEX_OFFICIAL_INDEX": "",
               "LABDEX_DEV_PLUGINS": str(ROOT / "plugins")}  # the official plugins, as if from the Marketplace
        srv = subprocess.Popen([sys.executable, "-m", "flask", "--app", "labdex:create_app()", "run", "--port", str(port)], cwd=ROOT / "backend", env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(60):
                try:
                    if requests.get(f"{base}/api/auth/status", timeout=1).ok:
                        break
                except requests.RequestException:
                    time.sleep(0.5)
            s = requests.Session()
            st = s.get(f"{base}/api/auth/status").json()
            user, pw = st["demo"]["username"], st["demo"]["password"]
            s.post(f"{base}/api/auth/login", json={"username": user, "password": pw}, headers=H).raise_for_status()
            seed(base, s)
            seed_macs(data)
            with sync_playwright() as p:
                b = p.chromium.launch()
                ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light", device_scale_factor=1)
                ctx.add_init_script("try{localStorage.setItem('labdex-theme','light')}catch(e){}")
                # the demo's banner isn't part of labdex
                ctx.add_init_script("document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement('style');s.textContent='.demobar{display:none!important}';document.head.append(s)})")
                pg = ctx.new_page()
                pg.request.post(f"{base}/api/auth/login", data={"username": user, "password": pw}, headers=H)
                for name, path, ready, action in SHOTS:
                    pg.goto(base + path)
                    try:
                        pg.wait_for_selector(ready, timeout=15000)
                    except Exception:
                        pg.screenshot(path=str(OUT / f"FAILED-{name}.png"))
                        raise
                    pg.wait_for_timeout(1500 if action == "wait" else 600)
                    if action == "trace":
                        pg.locator("tr", has_text="C-0102").locator("button[aria-label^='Trace']").click()
                        pg.wait_for_selector(".trace .path")
                        pg.wait_for_timeout(400)
                    pg.screenshot(path=str(OUT / f"{name}.png"))
                    print("  ", name)
                b.close()
        finally:
            srv.terminate()
            srv.wait(10)
    print(f"Wrote {len(SHOTS)} screenshots to {OUT}")


if __name__ == "__main__":
    main()
