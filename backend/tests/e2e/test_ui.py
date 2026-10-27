"""Browser tests of the built UI (Playwright, Chromium).

Run with LABDEX_E2E=1 after `npm run build` (CI does both). The server runs in
this process on a free port with the example inventory.
"""
import os
import threading

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LABDEX_E2E") != "1", reason="set LABDEX_E2E=1 (needs the built UI and Playwright)")

USER, PASSWORD = "admin", "correct-horse-battery"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from pathlib import Path
    from werkzeug.serving import make_server
    from labdex import STATIC_DIR, create_app, inventory
    from labdex.models import db
    if not (STATIC_DIR / "index.html").is_file():
        pytest.skip("UI not built (cd frontend && npm run build)")
    d = tmp_path_factory.mktemp("e2e")
    os.environ["LABDEX_ADMIN_USER"], os.environ["LABDEX_ADMIN_PASSWORD"] = USER, PASSWORD
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{d / 'e2e.db'}", "SECRET_KEY": "e2e", "LABDEX_PLUGIN_DIR": str(d / "p"),
                      "LABDEX_SCHEDULER": False, "LABDEX_AUTO_BACKUP": False})
    for k in ("LABDEX_ADMIN_USER", "LABDEX_ADMIN_PASSWORD"):
        os.environ.pop(k)  # only for this app's first start; don't leak into other tests
    with app.app_context():
        inventory.import_yaml((Path(__file__).parents[3] / "examples" / "homelab.yaml").read_text())
        db.session.remove()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def open_host(page, server, name, tab=""):
    """Open a host by name and wait until the page moved on to its stable id (it reloads once)."""
    page.goto(f"{server}/hosts/{name}{'?tab=' + tab if tab else ''}")
    page.wait_for_url(lambda u: f"/hosts/{name}" not in u)
    page.wait_for_selector(".builder" if tab == "overview" else ".tabs")


@pytest.fixture
def page(server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        p = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
        p.errors = []
        p.on("console", lambda m: m.type == "error" and p.errors.append(m.text))
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        p.goto(server + "/hosts")
        p.fill("input[autocomplete=username]", USER)
        p.fill("input[type=password]", PASSWORD)
        p.click("button.go")
        p.wait_for_selector("table tbody tr")
        yield p
        assert not p.errors, p.errors
        browser.close()


def test_hosts_search_open_and_back(page):
    page.click(".tabs >> text=Virtual")
    page.wait_for_selector("td >> text=srv-vm-dns-01")
    rows = page.locator("table tbody tr")
    total = rows.count()
    page.fill("input[aria-label=\"Search hosts\"]", "dns")
    page.wait_for_timeout(200)
    assert 0 < rows.count() < total
    rows.first.click()
    page.wait_for_selector(".glance")
    page.go_back()
    page.wait_for_selector("table tbody tr")
    assert page.input_value("input[aria-label=\"Search hosts\"]") == "dns"  # the list page was kept alive


def test_edit_host_and_see_it_in_the_list(page, server):
    page.goto(server + "/hosts/srv-vm-dns-01")
    page.wait_for_selector(".glance")
    r = page.evaluate("""async () => {
        const h = await (await fetch('/api/hosts/srv-vm-dns-01')).json()
        const res = await fetch('/api/hosts/srv-vm-dns-01', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'labdex'},
          body: JSON.stringify({...h, function: 'Resolver (e2e)'})})
        return res.status }""")
    assert r == 200
    page.goto(server + "/hosts?kind=virtual")
    page.wait_for_selector("text=Resolver (e2e) >> visible=true")


def test_change_elsewhere_arrives_live(page, server):
    """Another tab changes a host; the open list updates through the event stream, without reload."""
    page.fill("input[aria-label=\"Search hosts\"]", "sonos")
    page.wait_for_selector("table tbody tr")
    other = page.context.new_page()
    other.goto(server + "/api/hosts/sonos-living")
    other.evaluate("""async () => {
        const h = await (await fetch('/api/hosts/sonos-living')).json()
        await fetch('/api/hosts/sonos-living', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'labdex'},
          body: JSON.stringify({...h, function: 'Speaker (live)'})}) }""")
    page.wait_for_selector("text=Speaker (live) >> visible=true", timeout=8000)


@pytest.mark.parametrize("path, marker", [
    ("/", ".tiles .tile"), ("/networks", "table tbody tr"), ("/aliases", "table tbody tr"), ("/services", ".svc-card"),
    ("/admin/approvals", "text=Approval policies"), ("/admin/notifications", "text=Notification channels"),
    ("/lint", "h1"), ("/history", "h1"), ("/integrations", "h1"), ("/zones", "text=Areas and zones"),
    ("/naming", "text=Hostname parts"), ("/dns", "h1"), ("/naming?tab=tags", "text=Tags"), ("/admin/zones", "text=Areas and zones"), ("/features", "h1"),
    ("/about", "text=Changelog"), ("/topology", ".canvas canvas"), ("/networks/srv?tab=map", ".cell"), ("/admin/plugins", "text=Signing keys"),
    ("/policy?tab=firewalls", "text=No firewall enforces the matrix yet"), ("/plugins?tab=market", ".mcard"),
])
def test_pages_render(page, server, path, marker):
    page.goto(server + path)
    page.wait_for_selector(marker)


def test_vip_shows_on_members(page, server):
    r = page.evaluate("""async () => (await fetch('/api/hosts', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'labdex'},
        body: JSON.stringify({name: 'net-vip-k3s-01', kind: 'vip', vip_protocol: 'kube-vip', vip_members: ['srv-vm-k3s-01', 'srv-vm-k3s-02'],
                              interfaces: [{network: 'k8s', ip4: '10.10.128.10', primary: true}]})})).status""")
    assert r == 201
    page.goto(server + "/hosts/srv-vm-k3s-01?tab=network")
    page.wait_for_selector("text=Shared addresses (VIPs)")
    open_host(page, server, "net-vip-k3s-01", "overview")
    page.wait_for_selector(".chip:has-text('srv-vm-k3s-02')")


def test_host_picker_is_not_clipped(page, server):
    page.goto(server + "/hosts/new")
    page.wait_for_selector(".builder")
    page.select_option("select:near(:text('Kind'))", "vip")
    page.fill("input[placeholder='+ member']", "srv")
    page.wait_for_selector(".hp-drop li")
    assert page.evaluate("document.querySelector('.hp-drop').parentElement === document.body")


def test_host_dns_tab_and_fun_suggestions(page, server):
    open_host(page, server, "srv-vm-dns-01", "overview")
    page.click("button:has-text('Suggest')")
    page.wait_for_selector(".sugg .chip")
    page.locator(".sugg .chip").first.click()
    page.click(".tabs >> text=DNS")
    page.click("button:has-text('+ Alias')")
    page.locator("table.recs tbody tr").last.locator("input").first.fill("ns1")
    page.click("button:has-text('Save changes')")
    page.wait_for_selector("td >> text=ns1.lab.example.net")


def test_network_tabs(page, server):
    page.goto(server + "/networks/srv")
    page.wait_for_selector(".tabs >> text=Hosts")
    page.click(".tabs >> text=Usage")
    page.wait_for_selector("text=Next free (static)")
    page.click(".tabs >> text=Hosts")
    page.wait_for_selector("td >> text=srv-vm-dns-01")


def test_palette_finds_by_address_and_opens(page):
    page.keyboard.press("Control+k")
    page.wait_for_selector(".pal input")
    page.keyboard.type("10.10.100.53")
    page.wait_for_selector(".pal-r >> text=srv-vm-dns-01")
    page.keyboard.press("Enter")
    page.wait_for_selector(".glance")
    assert "/hosts/" in page.url and not page.url.endswith("srv-vm-dns-01")  # the stable id, not the name


def test_bulk_tag_two_hosts(page, server):
    boxes = page.locator("td.pick input")
    boxes.nth(0).click()
    boxes.nth(1).click()
    page.click(".bulkbar >> text=Change")
    page.locator(".dlg input[aria-label='Add a tag']").fill("bulky")
    page.keyboard.press("Enter")
    page.click(".dlg-foot >> text=Change 2 hosts")
    page.wait_for_selector(".bulkbar", state="detached")
    page.wait_for_selector("tbody .tagc >> text=bulky")
    assert page.locator("tbody .tagc >> text=bulky").count() == 2


def test_unsaved_changes_ask_before_leaving(page, server):
    open_host(page, server, "srv-vm-dns-01", "overview")
    for _ in range(100):  # loaded: the form holds the host (the CSP rules out wait_for_function)
        if page.input_value("input[placeholder='DNS resolver']"):
            break
        page.wait_for_timeout(50)
    page.fill("input[placeholder='DNS resolver']", "changed but not saved")
    page.click(".side >> text=Networks")
    page.wait_for_selector(".dlg >> text=Leave without saving?")
    page.click(".dlg-foot >> text=Cancel")
    assert "/hosts/" in page.url
    page.keyboard.press("Control+s")
    page.wait_for_selector("button:has-text('Saved')")


def test_theme_switch_sticks(page, server):
    page.click(".who > button")
    page.click(".themes >> text=Light")
    assert page.get_attribute("html", "data-theme") == "light"
    page.reload()
    page.wait_for_selector("table tbody tr")
    assert page.get_attribute("html", "data-theme") == "light"  # kept in this browser, applied before drawing
    page.click(".who > button")
    page.click(".themes >> text=Auto")
    assert page.get_attribute("html", "data-theme") is None


def test_rows_open_with_the_keyboard(page, server):
    page.goto(server + "/networks")
    page.wait_for_selector("table tbody tr[role=link]")
    page.focus("table tbody tr[role=link]")
    page.keyboard.press("Enter")
    page.wait_for_selector(".tabs >> text=Hosts")
    assert "/networks/" in page.url


def test_tabs_terms_and_groups(page, server):
    tabs = page.locator(".tabs a")
    assert [t.inner_text().split()[0] for t in tabs.all()] == ["Physical", "Virtual", "VIPs"]
    page.fill("input[aria-label=\"Search hosts\"]", "srv-vm-dns")
    page.wait_for_selector("td.empty >> text=1 in Virtual")  # the hit is in another tab: say where
    page.click("td.empty >> text=1 in Virtual")
    page.wait_for_selector("td >> text=srv-vm-dns-01")
    assert "kind=virtual" in page.url
    page.fill("input[aria-label=\"Search hosts\"]", "tag:admin")
    page.click(".tabs >> text=Physical")
    page.wait_for_selector("tbody .tagc >> text=admin")
    n = page.locator("table tbody tr").count()
    assert n >= 1 and all("admin" in r.inner_text() for r in page.locator("table tbody tr").all())
    page.goto(server + "/hosts?group=zone")
    page.wait_for_selector("tr.grph")
    assert page.locator("tr.grph").count() > 1


def test_draft_comes_back(page, server):
    open_host(page, server, "srv-vm-mon-01", "overview")
    for _ in range(100):
        if page.input_value("input[placeholder='DNS resolver']"):
            break
        page.wait_for_timeout(50)
    page.fill("input[placeholder='DNS resolver']", "draft kept")
    page.wait_for_timeout(1000)  # written after a short pause
    page.on("dialog", lambda d: d.accept())  # "leave the page?" from the browser
    page.goto(server + "/hosts")  # a full load: no guard in the way
    open_host(page, server, "srv-vm-mon-01", "overview")
    page.wait_for_selector(".draftbar >> text=Restore them")
    page.click(".draftbar >> text=Restore them")
    assert page.input_value("input[placeholder='DNS resolver']") == "draft kept"
    page.keyboard.press("Control+s")
    page.wait_for_selector("button:has-text('Saved')")
    open_host(page, server, "srv-vm-mon-01", "overview")
    page.wait_for_timeout(500)
    assert page.locator(".draftbar").count() == 0  # saving cleared it


def test_sidebar_marks_detail_pages(page, server):
    page.goto(server + "/hosts/srv-vm-dns-01")
    page.wait_for_selector(".side a[aria-current=page] >> text=Hosts")
    page.goto(server + "/networks/srv")
    page.wait_for_selector(".side a[aria-current=page] >> text=Networks")
    page.goto(server + "/topology")  # another section: Inventory closes, Network opens
    page.wait_for_selector(".side a[aria-current=page] >> text=Topology")
    assert page.locator(".side .grp .gh[aria-expanded=true]").count() == 1


def test_topology_views_and_cable_button(page, server):
    page.goto(server + "/topology")
    page.wait_for_selector(".canvas canvas")
    page.wait_for_selector("button:has-text('Cable')")
    # fun names are labels of their own, next to their hosts
    names = page.evaluate("() => document.querySelector('.canvas')._cyreg.cy.nodes('[type = \"name\"]').map((n) => [n.data('of'), n.data('text')])")
    assert names and all(of.startswith("h:") and text for of, text in names)
    page.click(".tabs >> text=Networks (L3 / VLANs)")
    page.wait_for_selector("text=in several networks")
    assert "view=l3" in page.url


def test_zone_page_by_its_id(page, server):
    page.goto(server + "/zones")
    page.wait_for_selector("a.zlink >> text=DC_TC")
    page.click("a.zlink >> text=DC_TC")
    page.wait_for_selector("h2 >> text=Networks")
    page.wait_for_selector("td >> text=srv")
    assert "/zones/" in page.url and not page.url.endswith("DC_TC")
    page.click("td >> text=srv")
    page.wait_for_selector(".tabs >> text=Hosts")
    assert "/networks/" in page.url and not page.url.endswith("/srv")


def test_dashboard_cards(page, server):
    page.goto(server + "/")
    page.wait_for_selector(".ditem .tile")
    page.click("a:has-text('Dashboard') >> nth=-1")  # "+ Dashboard"
    page.fill(".dlg input", "Links")
    page.click(".dlg-foot >> text=Create")
    page.wait_for_selector(".tabs >> text=Links")
    page.click("button:has-text('Edit')")
    page.click("button:has-text('Card')")
    page.click(".choice >> text=Links of your own")
    page.locator(".linkrow input").nth(0).fill("Jellyfin")
    page.locator(".linkrow input").nth(1).fill("javascript:alert(1)")
    page.wait_for_selector(".dlg .err >> text=an address starts with http")  # refused: only http(s) or a labdex page
    assert page.is_disabled(".dlg-foot >> text=Apply")
    page.locator(".linkrow input").nth(1).fill("https://jellyfin.example.net")
    page.click(".dlg-foot >> text=Apply")
    page.click("button:has-text('Done')")
    page.wait_for_selector("a.lk[href='https://jellyfin.example.net']")
    page.reload()
    page.wait_for_selector("a.lk >> text=Jellyfin")


def test_rack_put_a_host_in(page, server):
    r = page.evaluate("""async () => (await fetch('/api/racks', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'labdex'},
        body: JSON.stringify({name: 'R9', units: 12})})).status""")
    assert r == 201
    page.goto(server + "/racks")
    page.wait_for_selector(".rack >> text=R9")
    page.click(".rside >> nth=0 >> button.free[aria-label^='Front U12']")
    page.fill(".dlg input[placeholder^='Switch']", "net-sw-core")
    page.click(".hp-drop li >> text=net-sw-core-01")
    page.click(".dlg-foot >> text=Put it here")
    page.wait_for_selector(".dev >> text=net-sw-core-01")
    open_host(page, server, "net-sw-core-01")
    page.wait_for_selector(".glance >> text=R9")


def test_rack_panel(page, server):
    page.evaluate("""async () => fetch('/api/racks', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'labdex'},
        body: JSON.stringify({name: 'R8', units: 6})})""")
    page.goto(server + "/racks?site=")
    page.wait_for_selector(".rack >> text=R8")
    rack = page.locator(".rack", has_text="R8")
    rack.locator("button.free[aria-label^='Front U6']").click()
    page.click("text=A panel or something else")
    page.fill(".dlg input[placeholder='PP-A']", "PP-B")
    page.fill(".dlg input[placeholder^='24 ×']", "to the office")
    page.click(".dlg-foot >> text=Put it here")
    page.wait_for_selector(".item >> text=PP-B")


def test_vault_set_up_add_and_reveal(page, server):
    page.goto(server + "/vault")
    page.wait_for_selector("text=Set up the vault")
    pw = page.locator("input[type=password]")
    pw.nth(0).fill("blue horse staple battery")
    pw.nth(1).fill("blue horse staple battery")
    page.click("button:has-text('Set up the vault')")
    page.wait_for_selector("text=unsealed")
    open_host(page, server, "srv-vm-dns-01", "secrets")
    page.click("text=Secret for this host")
    page.fill(".dlg input[placeholder^='hosts/']", "hosts/srv-vm-dns-01/root")
    page.fill("input[aria-label='username value']", "root")
    page.fill("input[aria-label='password value']", "dns-root-pw-123")
    page.click(".dlg-foot >> text=Create")
    page.click("td >> text=hosts/srv-vm-dns-01/root")
    page.wait_for_selector(".fld >> text=password")
    assert page.locator("text=dns-root-pw-123").count() == 0
    page.click("button[aria-label='Reveal password']")
    page.fill("input[autocomplete=current-password]", PASSWORD)
    page.click(".dlg-foot >> text=Confirm")
    page.wait_for_selector(".fval >> text=dns-root-pw-123")


def test_bulk_add_interfaces(page, server):
    open_host(page, server, "net-sw-core-01", "network")
    page.click("button:has-text('Several')")
    page.fill("input[aria-label='Name pattern']", "xe-0/1/[0-3]")
    page.locator(".dlg select").nth(1).select_option("sfp+")
    assert page.locator(".dlg input[list='port-speeds']").input_value() == "10G"  # the connector's usual speed
    page.click(".dlg-foot >> text=Add 4")
    page.wait_for_selector(".li >> text=xe-0/1/3")
    assert page.locator(".ifgroup >> text=Physical").count() == 1
    page.click("button:has-text('Save changes')")
    page.wait_for_selector("button:has-text('Saved')")
    assert page.locator(".li >> text=sfp+ · 10G").count() == 4


def test_sidebar_sections_close_each_other(page, server):
    page.goto(server + "/hosts")
    page.wait_for_selector("table tbody tr")
    page.click("button.gh:has-text('Network')")
    page.wait_for_timeout(400)
    assert page.locator(".gw.open a:has-text('Topology')").count() == 1
    assert page.locator(".gw:not(.open) a:has-text('Hosts')").count() == 1  # Inventory closed, and out of the tab order
    assert page.locator(".gw:not(.open)").first.get_attribute("inert") is not None


def test_cable_list_label_and_port_map(page, server):
    r = page.evaluate("""async () => (await fetch('/api/topology/cables', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'labdex'},
        body: JSON.stringify({a: {host: 'net-sw-core-01', port: 'ge-0/0/1'}, b: {host: 'srv-pm-pve-01', port: 'eno1'}, speed: '10G'})})).status""")
    assert r == 201
    page.goto(server + "/cables")
    row = page.locator("tr", has_text="srv-pm-pve-01")
    row.locator("button[aria-label^='Edit']").click()
    page.click(".dlg button:has-text('Next free')")
    page.fill(".dlg input[placeholder='3']", "1.5")
    page.click(".dlg-foot >> text=Save")
    page.wait_for_selector("td >> text=C-0001")
    assert page.locator("td >> text=1.5 m").count() == 1
    # the switch's front panel: the cabled port is lit and names where it goes
    for n in range(2, 6):  # enough ports for a panel
        page.evaluate(f"""async () => fetch('/api/topology/cables', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Requested-With': 'labdex'}},
            body: JSON.stringify({{a: {{host: 'net-sw-core-01', port: 'ge-0/0/{n}'}}, b: {{host: 'net-sw-core-01', port: 'ge-0/0/{n + 10}'}}}})}})""")
    open_host(page, server, "net-sw-core-01")
    page.wait_for_selector(".hostdash .pm .port.on")
    assert page.locator(".hostdash .pm .port[aria-label*='srv-pm-pve-01 eno1']").count() == 1


def test_marketplace_install_and_uninstall(page, server):
    page.goto(server + "/plugins")
    page.wait_for_selector(".pcard")
    before = page.locator(".tabs a", has_text="Installed").inner_text().split()[-1]
    assert page.locator(".pcard .pname", has_text="OPNsense").count() == 0  # not installed on a new labdex
    page.click(".more a")  # "Browse the Marketplace"
    page.click(".seg button:has-text('Not installed')")
    card = page.locator(".mcard", has=page.locator("button.mname", has_text="OPNsense"))
    card.get_by_role("button", name="Install", exact=True).click()
    card.locator(".done").wait_for()
    page.click(".tabs a:has-text('Installed')")
    page.wait_for_selector(".pcard .pname:has-text('OPNsense')")
    page.click(".tabs a:has-text('Marketplace')")
    page.click("button.mname:has-text('OPNsense')")
    page.locator(".dlg-foot").get_by_role("button", name="Uninstall", exact=True).click()
    page.locator(".dlg-foot").last.get_by_role("button", name="Uninstall", exact=True).click()
    page.wait_for_selector(".dlg-foot >> role=button[name='Install']")
    page.wait_for_function("(n) => document.querySelector('.tabs a').innerText.trim().endsWith(' ' + n)", arg=before)
