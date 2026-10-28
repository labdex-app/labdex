<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, setUnauthorizedHandler } from './api'
import {
  AppWindow, Cable, ChevronRight, CircleCheck, GitFork, Github, Globe, History, Info, KeyRound, LayoutDashboard, Layers, LifeBuoy, ListTree, Menu, Network,
  GitPullRequestDraft, PanelLeftClose, PanelLeftOpen, Plug, Radar, Rows4, Search, Server, Settings, Shield, Tags, Waypoints,
} from 'lucide-vue-next'
import mark from './assets/mark.svg'
import CommandPalette from './components/CommandPalette.vue'
import Copy from './components/Copy.vue'
import { setTextSize, setTheme, textSize, theme } from './theme'
import ConfirmHost from './components/ConfirmHost.vue'
import SideTree from './components/SideTree.vue'
import Login from './views/Login.vue'
import { loadAuth, loadMeta, loadNav, refreshLint, refreshPending, stamp, startEvents, state, stopEvents } from './store'

const KEEP = ['Hosts', 'Networks', 'Services', 'Aliases', 'Lint', 'Dns', 'Discovery', 'History'] // pages kept alive between visits

const route = useRoute()
const error = ref('')
const menu = ref(false)
const drawer = ref(false) // mobile
let collapsed = false
try { collapsed = localStorage.getItem('labdex-side') === 'small' } catch { /* */ }
const small = ref(collapsed)
watch(small, (v) => { try { localStorage.setItem('labdex-side', v ? 'small' : 'wide') } catch { /* */ } })
watch(() => route.fullPath, () => { drawer.value = false; menu.value = false })

const G = (id, label, items) => ({ id, label, items: items.filter(Boolean) })
const groups = computed(() => {
  const n = state.nav
  return [
    G('inventory', 'Inventory', [
      { to: '/hosts', label: 'Hosts', icon: Server, count: state.meta?.counts.hosts },
      { to: '/networks', label: 'Networks', icon: Network, count: state.meta?.counts.networks },
      { to: '/racks', label: 'Racks', icon: Rows4 },
      { to: '/cables', label: 'Cables', icon: Cable },
      { to: '/services', label: 'Services', icon: AppWindow },
      { to: '/dns', label: 'DNS', icon: Globe },
      { to: '/naming', label: 'Naming', icon: Tags },
      { to: '/vault', label: 'Vault', icon: KeyRound },
    ]),
    G('network', 'Network', [
      { to: '/topology', label: 'Topology', icon: Waypoints },
      { to: '/zones', label: 'Zones', icon: Layers },
      { to: '/policy', label: 'Policy', icon: Shield },
      { to: '/aliases', label: 'Aliases', icon: ListTree },
      n?.show.discovery && { to: '/discovery', label: 'Discovery', icon: Radar },
    ]),
    G('automation', 'Automation', [
      { to: '/features', label: 'Features', icon: GitFork },
      { to: '/changesets', label: 'Change sets', icon: GitPullRequestDraft },
      n?.show.integrations && { to: '/plugins', label: 'Plugins', icon: Plug, pending: state.pending },
    ]),
    G('audit', 'Audit', [
      { to: '/lint', label: 'Lint', icon: CircleCheck, lint: true },
      { to: '/history', label: 'History', icon: History },
    ]),
  ]
})
const palette = ref(null)
const projectHost = computed(() => { try { return new URL(state.meta?.project_url || '').host } catch { return '' } })
const projectName = computed(() => ({ 'github.com': 'GitHub', 'gitlab.com': 'GitLab', 'codeberg.org': 'Codeberg' }[projectHost.value] || 'Source'))
const isMac = navigator.platform?.toLowerCase().includes('mac')
const pages = computed(() => [
  { to: '/', label: 'Dashboard', group: '' },
  ...groups.value.flatMap((g) => g.items.map((i) => ({ to: i.to, label: i.label, group: g.label }))),
  state.meta?.is_admin && { to: '/admin/general', label: 'Settings', group: '' },
  state.meta?.is_admin && { to: '/admin/users', label: 'Users', group: 'Settings' },
  { to: '/plugins?tab=market', label: 'Plugin Marketplace', group: 'Automation' },
  state.meta?.is_admin && { to: '/admin/plugins', label: 'Plugin repositories & upload', group: 'Settings' },
  state.meta?.is_admin && { to: '/admin/backup', label: 'Backup & restore', group: 'Settings' },
  { to: '/account', label: 'Password & API tokens', group: 'Account' },
  { to: '/help', label: 'Help', group: '' },
  { to: '/about', label: 'About labdex', group: '' },
].filter(Boolean))
// One section open at a time: the one of the page you're on opens by itself and closes the others;
// opening one by hand closes the rest too. Kept in this browser.
let savedOpen = ''
try { savedOpen = localStorage.getItem('labdex-side-group') || '' } catch { /* */ }
const openGroup = ref(savedOpen)
watch(openGroup, (v) => { try { localStorage.setItem('labdex-side-group', v) } catch { /* */ } })
// a list page and everything under it: /hosts is active on /hosts/srv-vm-dns-01 too
const active = (to) => route.path === to || route.path.startsWith(`${to}/`) || (to === '/plugins' && route.path.startsWith('/integrations'))
const inGroup = (g) => g.items.some((i) => active(i.to))
watch([() => route.path, groups], () => { const g = groups.value.find(inGroup); if (g) openGroup.value = g.id }, { immediate: true })
const isOpen = (g) => openGroup.value === g.id
const toggleGroup = (g) => { openGroup.value = isOpen(g) ? '' : g.id }
const groupBadge = (g) => {
  if (g.id === 'audit' && state.lint?.error) return { n: state.lint.error, cls: '' }
  if (g.id === 'audit' && state.lint?.warning) return { n: state.lint.warning, cls: 'warn' }
  if (g.id === 'automation' && state.pending) return { n: state.pending, cls: 'info' }
  return null
}
const treeOpen = ref((() => { try { return localStorage.getItem('labdex-side-tree') !== 'closed' } catch { return true } })())
watch(treeOpen, (v) => { try { localStorage.setItem('labdex-side-tree', v ? 'open' : 'closed') } catch { /* */ } })

// A newer labdex (administrators, when Settings › General has the release check on): a bar with the
// command to upgrade, until it's dismissed for that release in this browser.
const update = ref(null)
const DISMISSED = 'labdex-update-dismissed'
const dismissed = ref((() => { try { return localStorage.getItem(DISMISSED) || '' } catch { return '' } })())
const showUpdate = computed(() => update.value && update.value.tag !== dismissed.value)
const upgradeDocs = computed(() => { // the docs site for a GitHub project (like Help), else the project's README
  const m = (state.meta?.project_url || '').match(/^https:\/\/github\.com\/([^/]+)\/([^/]+)$/)
  return m ? `https://${m[1]}.github.io/${m[2]}/operate/upgrading/` : `${state.meta?.project_url}#readme`
})
function dismissUpdate() {
  dismissed.value = update.value.tag
  try { localStorage.setItem(DISMISSED, dismissed.value) } catch { /* */ }
}
async function loadUpdate() {
  if (!state.meta?.is_admin) return
  try { update.value = (await api.get('/api/about/update')).update } catch { /* the About page shows why */ }
}

async function start() {
  error.value = ''
  try {
    const st = await loadAuth()
    if (!st.user) return
    await loadMeta()
    refreshLint()
    refreshPending()
    loadNav()
    loadUpdate()
    startEvents()
  } catch (e) {
    error.value = `Can't reach the labdex API: ${e.message}`
  }
}

async function logout() {
  menu.value = false
  stopEvents()
  await api.post('/api/auth/logout').catch(() => {})
  state.meta = null
  await loadAuth()
}

setUnauthorizedHandler(() => {
  if (state.auth?.user) { stopEvents(); state.meta = null; loadAuth() }
})

function sideKey(e) {
  if ((e.key === 'b' || e.key === 'B') && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) { e.preventDefault(); small.value = !small.value }
}
onMounted(() => { start(); window.addEventListener('keydown', sideKey) })
</script>

<template>
  <div v-if="error" class="page"><div class="errbox">{{ error }} <button class="btn small" @click="start">Retry</button></div></div>
  <Login v-else-if="state.auth && !state.auth.user" @done="start" />
  <div v-else-if="state.meta" class="shell" :class="{ small, drawer }">
    <a href="#main" class="skip">Skip to the page</a>
    <aside class="side">
      <div class="side-head">
        <RouterLink to="/" class="brand" :title="state.nav?.instance_name"><img :src="mark" alt="" width="22" height="22"><span class="wm">lab<span>dex</span></span></RouterLink>
        <span v-if="state.nav?.instance_name && state.nav.instance_name !== 'labdex' && !small" class="inst">{{ state.nav.instance_name }}</span>
        <span class="grow"></span>
        <button class="icon-btn hide-m" :title="small ? 'Expand the sidebar' : 'Collapse the sidebar'" :aria-label="small ? 'Expand the sidebar' : 'Collapse the sidebar'" @click="small = !small">
          <PanelLeftOpen v-if="small" :size="17" /><PanelLeftClose v-else :size="17" /></button>
      </div>
      <button class="find" :title="`Search and go (${isMac ? '⌘' : 'Ctrl'} K)`" @click="palette?.show()">
        <Search :size="15" /><span class="lb">Search</span><span class="grow"></span><kbd v-if="!small" class="lb">{{ isMac ? '⌘' : 'Ctrl' }} K</kbd>
      </button>
      <nav class="side-scroll">
        <div class="side-links">
          <RouterLink to="/" class="nl" title="Dashboard" exact-active-class="router-link-active" active-class="">
            <LayoutDashboard class="ic" :size="17" /><span class="lb">Dashboard</span>
            <span v-if="state.pending" class="pill info" :title="`${state.pending} plan(s) waiting for review`">{{ state.pending }}</span>
          </RouterLink>
          <section v-for="g in groups" :key="g.id" class="grp">
            <button v-if="!small" class="gh" :aria-expanded="isOpen(g)" @click="toggleGroup(g)">
              <ChevronRight class="car" :class="{ open: isOpen(g) }" :size="14" />
              <span class="lb">{{ g.label }}</span>
              <span v-if="!isOpen(g) && groupBadge(g)" class="pill" :class="groupBadge(g).cls">{{ groupBadge(g).n }}</span>
            </button>
            <div class="gw" :class="{ open: small || isOpen(g) }" :inert="!(small || isOpen(g))"><div class="gi">
              <RouterLink v-for="n in g.items" :key="n.to" :to="n.to" class="nl" active-class="" exact-active-class=""
                          :class="{ 'router-link-active': active(n.to) }" :aria-current="active(n.to) ? 'page' : undefined" :title="n.label">
                <component :is="n.icon" class="ic" :size="17" />
                <span class="lb">{{ n.label }}</span>
                <span v-if="n.count != null" class="count">{{ n.count }}</span>
                <span v-if="n.lint && state.lint?.error" class="pill">{{ state.lint.error }}</span>
                <span v-else-if="n.lint && state.lint?.warning" class="pill warn">{{ state.lint.warning }}</span>
                <span v-if="n.pending" class="pill info" :title="`${n.pending} plan(s) waiting for review`">{{ n.pending }}</span>
              </RouterLink>
            </div></div>
          </section>
        </div>
        <!-- the zone tree scrolls on its own; its scrollbar space is reserved so the width never jumps -->
        <section v-if="!small" class="side-zones" :class="{ shut: !treeOpen }">
          <button class="gh" :aria-expanded="treeOpen" @click="treeOpen = !treeOpen">
            <ChevronRight class="car" :class="{ open: treeOpen }" :size="14" /><span class="lb">Zones</span>
          </button>
          <SideTree v-if="treeOpen" />
        </section>
      </nav>
    </aside>
    <div class="scrim" @click="drawer = false"></div>
    <ConfirmHost />
    <CommandPalette ref="palette" :pages="pages" />
    <main id="main" class="main" tabindex="-1">
      <header class="tbar">
        <button class="icon-btn show-m" aria-label="Menu" @click="drawer = true"><Menu :size="18" /></button>
        <RouterLink to="/" class="brand show-m"><img :src="mark" alt="" width="22" height="22"><span class="wm">lab<span>dex</span></span></RouterLink>
        <span class="grow"></span>
        <button class="icon-btn show-m" aria-label="Search" @click="palette?.show()"><Search :size="18" /></button>
        <nav class="tlinks" aria-label="labdex">
          <RouterLink to="/help" class="tl" title="Help"><LifeBuoy :size="16" /><span class="tlb">Help</span></RouterLink>
          <a :href="state.meta.project_url" class="tl" target="_blank" rel="noopener" :title="`labdex on ${projectHost}`"><Github v-if="projectHost === 'github.com'" :size="16" /><GitFork v-else :size="16" /><span class="tlb">{{ projectName }}</span></a>
          <RouterLink to="/about" class="tl" :title="`About labdex ${state.meta.version}`"><Info :size="16" /><span class="tlb">About</span></RouterLink>
          <RouterLink v-if="state.meta.is_admin" to="/admin" class="tl" :class="{ on: $route.path.startsWith('/admin') }" title="Settings"><Settings :size="16" /><span class="tlb">Settings</span></RouterLink>
        </nav>
        <div class="who">
          <button class="avbtn" :aria-expanded="menu" :aria-label="`Account: ${state.auth.user.display_name}`" :title="state.auth.user.display_name" @click="menu = !menu">
            <span class="avatar">{{ state.auth.user.display_name.slice(0, 1).toUpperCase() }}</span></button>
          <div v-if="menu" class="menu pop" @keydown.esc="menu = false">
            <div class="mhead"><b>{{ state.auth.user.display_name }}</b>
              <div class="small faint">{{ state.auth.user.username !== state.auth.user.display_name ? `${state.auth.user.username} · ` : '' }}{{ state.auth.user.is_admin ? 'administrator' : (state.auth.user.groups.join(', ') || 'user') }}</div></div>
            <div class="themes" role="radiogroup" aria-label="Theme">
              <span class="small faint">Theme</span>
              <div class="seg">
                <button v-for="t in ['system', 'light', 'dark']" :key="t" type="button" role="radio" :aria-checked="theme === t" :class="{ on: theme === t }"
                        @click="setTheme(t)">{{ t === 'system' ? 'Auto' : t[0].toUpperCase() + t.slice(1) }}</button>
              </div>
            </div>
            <div class="themes" role="radiogroup" aria-label="Text size">
              <span class="small faint">Text</span>
              <div class="seg">
                <button v-for="t in ['compact', 'comfortable', 'large']" :key="t" type="button" role="radio" :aria-checked="textSize === t" :class="{ on: textSize === t }"
                        :title="{ compact: '14 px', comfortable: '15 px', large: '16 px' }[t]" @click="setTextSize(t)">{{ t[0].toUpperCase() + t.slice(1) }}</button>
              </div>
            </div>
            <RouterLink to="/account">Password &amp; API tokens</RouterLink>
            <RouterLink class="show-m" to="/help">Help</RouterLink>
            <RouterLink class="show-m" to="/about">About labdex {{ state.meta.version }}</RouterLink>
            <RouterLink v-if="state.meta.is_admin" class="show-m" to="/admin">Settings</RouterLink>
            <button v-if="state.auth.user.via === 'session'" @click="logout">Sign out</button>
          </div>
        </div>
      </header>
      <div v-if="menu" class="menu-scrim" @click="menu = false"></div>
      <div v-if="showUpdate" class="updatebar" role="status">
        <span>labdex <strong>{{ update.tag }}</strong> is out (this is {{ state.meta.version }}).
          <a :href="update.url" target="_blank" rel="noopener">Release notes ↗</a></span>
        <span>Upgrade: <Copy :text="update.command" /> <a :href="upgradeDocs" target="_blank" rel="noopener">How to upgrade ↗</a></span>
        <button class="btn small" @click="dismissUpdate">Dismiss</button>
      </div>
      <div v-if="state.meta.demo" class="demobar">Demo: a sample lab with a pretend DNS server and monitor. Resets {{ stamp(state.meta.demo.resets_at) }}.</div>
      <div v-if="state.nav && !state.nav.zones_defined && !($route.path.startsWith('/admin') || $route.path === '/zones')" class="page" style="padding-bottom: 0">
        <div class="warnbox">
          No zones yet: networks and hosts need an area (and, if you like, zones inside it) first.
          <RouterLink v-if="state.meta.is_admin" to="/zones">Set up zones →</RouterLink>
          <span v-else>Ask an administrator.</span>
        </div>
      </div>
      <!-- List pages stay alive: back from a host is instant and keeps scroll, filters and the cached rows.
           They refresh from the query cache when shown again. -->
      <RouterView v-slot="{ Component, route: r }">
        <KeepAlive :include="KEEP" :max="6">
          <component :is="Component" :key="r.path" />
        </KeepAlive>
      </RouterView>
    </main>
  </div>

  <div v-if="state.toast" class="toast" :class="state.toast.kind" role="status">{{ state.toast.text }}</div>
</template>

<style scoped>
.skip { position: absolute; left: 8px; top: -40px; z-index: 100; background: var(--accent); color: #fff; padding: 6px 12px; border-radius: var(--r-ctl); }
.skip:focus { top: 8px; }
.main:focus { outline: none; }
.themes { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 12px 8px; }
.themes .seg button { padding: 3px 10px; font-size: calc(12px * var(--text)); }
.find { display: flex; align-items: center; gap: 8px; margin: 2px 10px 6px; padding: 6px 10px; border: 1px solid var(--line-strong); border-radius: var(--r-ctl);
  background: var(--bg); color: var(--faint); font: inherit; font-size: calc(13px * var(--text)); cursor: pointer; }
.find:hover { color: var(--ink); border-color: var(--accent); }
.find kbd { font-family: var(--mono); font-size: calc(11px * var(--text)); color: var(--faint); }
.small .find { justify-content: center; margin: 2px 6px 6px; }
.small .find .lb { display: none; }
.shell { display: grid; grid-template-columns: 248px minmax(0, 1fr); min-height: 100vh; }
.shell.small { grid-template-columns: 56px minmax(0, 1fr); }
.side { position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column; background: var(--panel);
  border-right: 1px solid var(--line); z-index: 20; }
.side-head { display: flex; align-items: center; gap: 8px; padding: 12px 12px 8px 16px; }
.brand { display: inline-flex; align-items: center; gap: 8px; font-family: var(--disp); font-weight: 600; font-size: calc(17px * var(--text)); color: var(--ink); letter-spacing: -.01em; }
.brand .wm span { color: var(--accent-soft); }
.brand:hover { text-decoration: none; }
.inst { font-size: calc(12px * var(--text)); color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.side-scroll { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 0 8px; }
.side-links { flex: none; max-height: 66vh; overflow-y: auto; padding: 2px 8px 6px; scrollbar-width: thin; }
.side-zones { flex: 1; min-height: 120px; overflow-y: auto; scrollbar-gutter: stable; padding: 0 2px 0 8px; scrollbar-width: thin;
  border-top: 1px solid var(--line); }
.side-zones.shut { flex: none; min-height: 0; }
.side-zones > .gh { position: sticky; top: 0; background: var(--panel); z-index: 3; }
.grp { margin-top: 6px; }
.gh { width: 100%; display: flex; align-items: center; gap: 8px; font: inherit; font-weight: 600; color: var(--ink);
  background: none; border: 0; padding: 7px 8px; cursor: pointer; border-radius: var(--r-ctl); text-align: left; }
.gh:hover { background: var(--raise); }
.gh .car { color: var(--faint); }
/* a section opens and closes by its own height: the grid row goes from 0fr to 1fr */
.gw { display: grid; grid-template-rows: 0fr; transition: grid-template-rows .22s ease, opacity .22s ease; opacity: 0; }
.gw.open { grid-template-rows: 1fr; opacity: 1; }
.gw > .gi { min-height: 0; overflow: hidden; }
@media (prefers-reduced-motion: reduce) { .gw { transition: none; } }
.gi { padding-left: 6px; margin-left: 14px; border-left: 1px solid var(--line); }
.car { transition: transform .15s; flex: none; }
.car.open { transform: rotate(90deg); }
.gi { display: flex; flex-direction: column; gap: 1px; }
.small .gi { padding-left: 0; margin-left: 0; border-left: 0; }
.nl { width: 100%; font: inherit; background: none; border: 0; cursor: pointer; display: flex; align-items: center; gap: 10px;
  color: var(--muted); font-weight: 500; padding: 6px 8px; border-radius: var(--r-ctl); white-space: nowrap; text-align: left; }
.nl:hover { color: var(--ink); text-decoration: none; background: var(--raise); }
.nl.router-link-active { color: var(--ink); background: var(--accent-faint); }
.ic { color: var(--faint); flex: none; }
.nl.router-link-active .ic { color: var(--accent-soft); }
.lb { flex: 1; overflow: hidden; text-overflow: ellipsis; }
.count { color: var(--faint); font-weight: 400; font-size: calc(12px * var(--text)); font-variant-numeric: tabular-nums; }
.tbar { display: flex; align-items: center; gap: 6px; padding: 6px 16px 6px 20px; min-height: 46px; border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 12; background: var(--bar-bg, var(--bg)); }
.tlinks { display: flex; align-items: center; gap: 2px; }
.tl { display: inline-flex; align-items: center; gap: 6px; padding: 5px 9px; border-radius: var(--r-ctl); color: var(--muted); font-size: calc(13px * var(--text)); font-weight: 500; }
.tl:hover { color: var(--ink); background: var(--raise); text-decoration: none; }
.tl.router-link-active, .tl.on { color: var(--ink); background: var(--accent-faint); }
.who { position: relative; margin-left: 6px; }
.avbtn { background: none; border: 0; padding: 2px; border-radius: 50%; cursor: pointer; display: grid; }
.avbtn:hover .avatar, .avbtn[aria-expanded="true"] .avatar { box-shadow: 0 0 0 3px var(--accent-faint); }
.avbtn .avatar { width: 28px; height: 28px; font-size: calc(12.5px * var(--text)); margin: 0; }
.mhead { padding: 8px 12px 6px; border-bottom: 1px solid var(--line); margin-bottom: 4px; }
.menu-scrim { position: fixed; inset: 0; z-index: 11; }
.show-m { display: none !important; }
.avatar { width: 22px; height: 22px; border-radius: 50%; background: var(--accent); color: #fff; display: inline-grid; place-items: center;
  font-size: calc(11px * var(--text)); font-weight: 700; flex: none; margin-left: -2px; }
.menu { position: absolute; right: 0; top: 38px; z-index: 30; min-width: 240px; }
.menu a, .menu button { display: block; width: 100%; text-align: left; font: inherit; color: var(--ink); background: none; border: 0;
  padding: 7px 12px; border-radius: var(--r-ctl); cursor: pointer; }
.menu a:hover, .menu button:hover { background: var(--raise); text-decoration: none; }
.icon-btn { font: inherit; background: none; border: 1px solid transparent; color: var(--faint); cursor: pointer; border-radius: var(--r-ctl); padding: 4px; display: inline-grid; place-items: center; }
.icon-btn:hover { color: var(--ink); border-color: var(--line); }
.small .lb, .small .count, .small .pill, .small .brand .wm { display: none; }
.small .grp { margin-top: 0; padding-top: 6px; border-top: 1px solid var(--line); }
.small .grp:first-of-type { border-top: 0; }
.small .side-head { padding: 12px 8px 8px; flex-direction: column; }
.small .nl { justify-content: center; }
.scrim { display: none; }
.main { min-width: 0; }
.toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%); z-index: 50; background: var(--accent); color: #fff;
  padding: 9px 16px; border-radius: var(--r-box); font-weight: 600; box-shadow: 0 8px 24px var(--shadow); max-width: min(90vw, 640px); }
.toast.error { background: var(--err-solid); }
@media (max-width: 860px) {
  .shell, .shell.small { grid-template-columns: minmax(0, 1fr); }
  .side { position: fixed; left: 0; top: 0; width: 272px; transform: translateX(-100%); transition: transform .18s; }
  .drawer .side { transform: none; }
  .drawer .scrim { display: block; position: fixed; inset: 0; background: var(--scrim); z-index: 15; }
  .small .lb, .small .count, .small .pill { display: initial; }

  .tbar { padding: 8px 12px; border-top: 3px solid var(--accent); gap: 10px; }
  .tlinks { display: none; }
  .show-m { display: revert !important; }
  .menu a.show-m { display: block !important; }
}
</style>
