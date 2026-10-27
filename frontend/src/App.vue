<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, setUnauthorizedHandler } from './api'
import {
  AppWindow, Cable, ChevronRight, ChevronsLeft, ChevronsRight, CircleCheck, GitFork, Globe, History, KeyRound, LayoutDashboard, Layers, ListTree, Menu, Network, PanelLeftClose,
  PanelLeftOpen, Plug, Radar, Rows4, Search, Server, Settings, Shield, Tags, Waypoints,
} from 'lucide-vue-next'
import CommandPalette from './components/CommandPalette.vue'
import { setTheme, theme } from './theme'
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
      n?.show.integrations && { to: '/plugins', label: 'Plugins', icon: Plug, pending: state.pending },
    ]),
    G('audit', 'Audit', [
      { to: '/lint', label: 'Lint', icon: CircleCheck, lint: true },
      { to: '/history', label: 'History', icon: History },
    ]),
  ]
})
const palette = ref(null)
const isMac = navigator.platform?.toLowerCase().includes('mac')
const pages = computed(() => [
  { to: '/', label: 'Dashboard', group: '' },
  ...groups.value.flatMap((g) => g.items.map((i) => ({ to: i.to, label: i.label, group: g.label }))),
  state.meta?.is_admin && { to: '/admin/general', label: 'Admin', group: '' },
  state.meta?.is_admin && { to: '/admin/users', label: 'Users', group: 'Admin' },
  { to: '/plugins?tab=market', label: 'Plugin Marketplace', group: 'Automation' },
  state.meta?.is_admin && { to: '/admin/plugins', label: 'Plugin repositories & upload', group: 'Admin' },
  state.meta?.is_admin && { to: '/admin/backup', label: 'Backup & restore', group: 'Admin' },
  { to: '/account', label: 'Password & API tokens', group: 'Account' },
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

async function start() {
  error.value = ''
  try {
    const st = await loadAuth()
    if (!st.user) return
    await loadMeta()
    refreshLint()
    refreshPending()
    loadNav()
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
        <RouterLink to="/" class="brand" :title="state.nav?.instance_name">lab<span>dex</span></RouterLink>
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
      <div class="side-foot">
        <button class="nl hide-m" :title="small ? 'Expand the sidebar (Ctrl B)' : 'Collapse the sidebar (Ctrl B)'" @click="small = !small">
          <ChevronsRight v-if="small" class="ic" :size="17" /><ChevronsLeft v-else class="ic" :size="17" /><span class="lb">Collapse</span></button>
        <RouterLink v-if="state.meta.is_admin" to="/admin" class="nl" :class="{ 'router-link-active': $route.path.startsWith('/admin') }" title="Admin">
          <Settings class="ic" :size="17" /><span class="lb">Admin</span></RouterLink>
        <div class="who">
          <button class="nl" :aria-expanded="menu" @click="menu = !menu" :title="state.auth.user.display_name">
            <span class="avatar">{{ state.auth.user.display_name.slice(0, 1).toUpperCase() }}</span>
            <span class="lb">{{ state.auth.user.display_name }}</span>
          </button>
          <div v-if="menu" class="menu">
            <div class="small faint" style="padding: 6px 12px">
              {{ state.auth.user.username }} · {{ state.auth.user.is_admin ? 'administrator' : (state.auth.user.groups.join(', ') || 'user') }}
            </div>
            <div class="themes" role="radiogroup" aria-label="Theme">
              <span class="small faint">Theme</span>
              <div class="seg">
                <button v-for="t in ['system', 'light', 'dark']" :key="t" type="button" role="radio" :aria-checked="theme === t" :class="{ on: theme === t }"
                        @click="setTheme(t)">{{ t === 'system' ? 'Auto' : t[0].toUpperCase() + t.slice(1) }}</button>
              </div>
            </div>
            <RouterLink to="/account">Password &amp; API tokens</RouterLink>
            <RouterLink to="/about">About labdex {{ state.meta.version }}</RouterLink>
            <button v-if="state.auth.user.via === 'session'" @click="logout">Sign out</button>
          </div>
        </div>
      </div>
    </aside>
    <div class="scrim" @click="drawer = false"></div>
    <ConfirmHost />
    <CommandPalette ref="palette" :pages="pages" />
    <main id="main" class="main" tabindex="-1">
      <div class="mbar">
        <button class="icon-btn" aria-label="Menu" @click="drawer = true"><Menu :size="18" /></button>
        <RouterLink to="/" class="brand">lab<span>dex</span></RouterLink>
        <span class="grow"></span>
        <button class="icon-btn" aria-label="Search" @click="palette?.show()"><Search :size="18" /></button>
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
.themes .seg button { padding: 3px 10px; font-size: 12px; }
.find { display: flex; align-items: center; gap: 8px; margin: 2px 10px 6px; padding: 6px 10px; border: 1px solid var(--line-strong); border-radius: var(--r-ctl);
  background: var(--bg); color: var(--faint); font: inherit; font-size: 13px; cursor: pointer; }
.find:hover { color: var(--ink); border-color: var(--accent); }
.find kbd { font-family: var(--mono); font-size: 11px; color: var(--faint); }
.small .find { justify-content: center; margin: 2px 6px 6px; }
.small .find .lb { display: none; }
.shell { display: grid; grid-template-columns: 248px minmax(0, 1fr); min-height: 100vh; }
.shell.small { grid-template-columns: 56px minmax(0, 1fr); }
.side { position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column; background: var(--panel);
  border-right: 1px solid var(--line); z-index: 20; }
.side-head { display: flex; align-items: center; gap: 8px; padding: 12px 12px 8px 16px; }
.brand { font-weight: 600; font-size: 17px; color: var(--ink); letter-spacing: -.01em; }
.brand span { color: var(--accent-soft); }
.brand:hover { text-decoration: none; }
.inst { font-size: 12px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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
.count { color: var(--faint); font-weight: 400; font-size: 12px; font-variant-numeric: tabular-nums; }
.side-foot { border-top: 1px solid var(--line); padding: 8px; }
.who { position: relative; }
.avatar { width: 22px; height: 22px; border-radius: 50%; background: var(--accent); color: #fff; display: inline-grid; place-items: center;
  font-size: 11px; font-weight: 700; flex: none; margin-left: -2px; }
.menu { position: absolute; left: 4px; bottom: 44px; min-width: 220px; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 4px; box-shadow: 0 12px 30px var(--shadow); }
.menu a, .menu button { display: block; width: 100%; text-align: left; font: inherit; color: var(--ink); background: none; border: 0;
  padding: 7px 12px; border-radius: 7px; cursor: pointer; }
.menu a:hover, .menu button:hover { background: var(--raise); text-decoration: none; }
.icon-btn { font: inherit; background: none; border: 1px solid transparent; color: var(--faint); cursor: pointer; border-radius: 6px; padding: 4px; display: inline-grid; place-items: center; }
.icon-btn:hover { color: var(--ink); border-color: var(--line); }
.small .lb, .small .count, .small .pill, .small .brand span { display: none; }
.small .grp { margin-top: 0; padding-top: 6px; border-top: 1px solid var(--line); }
.small .grp:first-of-type { border-top: 0; }
.small .side-head { padding: 12px 8px 8px; flex-direction: column; }
.small .nl { justify-content: center; }
.mbar, .scrim { display: none; }
.main { min-width: 0; }
.toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%); z-index: 50; background: var(--accent); color: #fff;
  padding: 9px 16px; border-radius: 9px; font-weight: 600; box-shadow: 0 8px 24px var(--shadow); max-width: min(90vw, 640px); }
.toast.error { background: #b91c1c; }
@media (max-width: 860px) {
  .shell, .shell.small { grid-template-columns: minmax(0, 1fr); }
  .side { position: fixed; left: 0; top: 0; width: 272px; transform: translateX(-100%); transition: transform .18s; }
  .drawer .side { transform: none; }
  .drawer .scrim { display: block; position: fixed; inset: 0; background: var(--scrim); z-index: 15; }
  .small .lb, .small .count, .small .pill { display: initial; }

  .mbar { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--line); border-top: 3px solid var(--accent);
    position: sticky; top: 0; background: var(--bar-bg); z-index: 10; }
}
</style>
