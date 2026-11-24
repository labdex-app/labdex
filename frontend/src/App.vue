<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, setUnauthorizedHandler } from './api'
import { AlertTriangle, CheckCircle2, ChevronRight, ChevronsUpDown, GitPullRequestArrow, Inbox, Layers, Menu, Network, PanelLeftClose, PanelLeftOpen, Search, Server, Settings, SquareStack, Star, Tags, X } from 'lucide-vue-next'
import mark from './assets/mark.svg'
import CommandPalette from './components/CommandPalette.vue'
import Copy from './components/Copy.vue'
import StatusBar from './components/StatusBar.vue'
import { setTextSize, setTheme, textSize, theme } from './theme'
import ConfirmHost from './components/ConfirmHost.vue'
import Login from './views/Login.vue'
import { SECTIONS, entryFor } from './nav'
import { isPinned, loadAuth, loadMeta, loadNav, loadSidebar, refreshBar, refreshLint, refreshPending, setSite, stamp, startEvents, state, stopEvents, togglePin } from './store'

const KEEP = ['Hosts', 'Networks', 'Services', 'Patches', 'Agents', 'Aliases', 'Lint', 'Dns', 'Discovery', 'History'] // pages kept alive between visits

const route = useRoute()
const router = useRouter()
const error = ref('')
const retrying = ref(false)
const menu = ref(false)
const drawer = ref(false) // mobile
let collapsed = false
try { collapsed = localStorage.getItem('labdex-side') === 'small' } catch { /* */ }
const small = ref(collapsed)
watch(small, (v) => { try { localStorage.setItem('labdex-side', v ? 'small' : 'wide') } catch { /* */ } })
watch(() => route.fullPath, () => { drawer.value = false; menu.value = false })

const shows = (what) => !what || Boolean(state.nav?.show[what])
const inboxCount = computed(() => (state.lint?.error || 0) + (state.lint?.warning || 0))
const badge = (i) => (i.badge === 'inbox' ? inboxCount.value : i.badge === 'pending' ? state.pending : 0)
// your sidebar: pages you hid are left out (still in Ctrl K), and your order wins inside each group
const rank = (to) => { const n = state.sidebar.order.indexOf(to); return n < 0 ? 1000 : n }
const groups = computed(() => SECTIONS.map((g) => ({
  ...g, items: g.items.filter((i) => shows(i.show) && !state.sidebar.hidden.includes(i.to))
    .map((i, n) => ({ i, n })).sort((a, b) => rank(a.i.to) - rank(b.i.to) || a.n - b.n).map((x) => x.i),
})).filter((g) => g.items.length))

// pins: anything with an address, shown under Overview and Inbox; hosts carry their live dot
const PIN_ICON = { host: Server, network: Network, zone: Layers, service: SquareStack, change: GitPullRequestArrow, view: Tags, page: Star }
const live = computed(() => {
  const m = {}
  for (const z of state.nav?.tree || []) for (const h of [...z.hosts, ...z.networks.flatMap((x) => x.hosts)]) { m[h.name] = h.live; if (h.fun) m[h.fun] ??= h.live }
  return m
})
const LIVE_DOT = { up: 'ok', down: 'err', degraded: 'warn' }
const pinActive = (p) => route.fullPath === p.to || route.path === p.to

// the site switcher: pages that can filter by site get it in their link
const sites = computed(() => state.meta?.sites || [])
const siteName = computed(() => sites.value.find((x) => x.code === state.site)?.name || 'All sites')
const SITED = ['/hosts', '/networks']
const link = (to) => (state.site && SITED.includes(to) ? { path: to, query: { site: state.site } } : to)
function pickSite(code) {
  setSite(code)
  if (SITED.includes(route.path)) router.replace({ query: { ...route.query, site: code || undefined } })
}
watch(sites, (list) => { if (state.site && list.length && !list.some((x) => x.code === state.site)) setSite('') })

// the item's own name for the last crumb: pages put it first in the window title ("web-01 · Host · labdex"),
// while the address holds its stable id
const titleName = ref('')
function readTitle() { const parts = document.title.split(' · '); titleName.value = parts.length >= 3 ? parts[0] : '' }
onMounted(() => {
  const el = document.querySelector('title')
  if (el) new MutationObserver(readTitle).observe(el, { childList: true, characterData: true, subtree: true })
})

const entry = computed(() => entryFor(route.path))

// the star in the top bar pins what's on screen: an item, a page, or a filtered list (a saved view)
const here = computed(() => {
  const { peek: _p, job: _j, ...q } = route.query
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => typeof v === 'string' && v)).toString()
  const to = route.path + (qs ? `?${qs}` : '')
  const p = route.path
  const kind = p.startsWith('/hosts/') ? 'host' : p.startsWith('/networks/') ? 'network' : p.startsWith('/zones/') ? 'zone'
    : p.startsWith('/jobs/') ? 'change' : qs ? 'view' : 'page'
  const last = crumbs.value[crumbs.value.length - 1]?.label || p
  const label = kind === 'view' ? `${last} · ${Object.values(q).filter((v) => typeof v === 'string').join(' ')}` : last
  return { to, kind, label: label.slice(0, 80), key: kind === 'host' ? last : '' }
})
const canPin = computed(() => route.path !== '/' && route.params.name !== 'new' && !route.path.startsWith('/account/sidebar'))
const active = (i) => entry.value === i
const tabs = computed(() => (entry.value?.tabs || []).filter((t) => shows(t.show)))
const tabOn = (t) => route.path === t.to || route.path.startsWith(`${t.to}/`)
// site / area / page / item, like a path on a drawing
const crumbs = computed(() => {
  const out = [{ label: siteName.value }]
  const e = entry.value
  if (e && e.to !== '/') out.push({ label: e.label, to: link(e.to) })
  const tab = tabs.value.find(tabOn)
  if (tab && tab.label !== e?.label && tabs.value[0] !== tab) out.push({ label: tab.label, to: tab.to })
  if (!e) out.push({ label: route.meta.title || '' })
  const name = route.params.name || route.params.id
  if (name) out.push({ label: name === 'new' ? 'New' : (titleName.value || String(name)) })
  return out.filter((c) => c.label)
})

const palette = ref(null)
const isMac = navigator.platform?.toLowerCase().includes('mac')
const pages = computed(() => [
  ...groups.value.flatMap((g) => g.items.flatMap((i) => [
    { to: i.to, label: i.label, group: g.label },
    ...(i.tabs || []).filter((t) => shows(t.show) && t.to !== i.to).map((t) => ({ to: t.to, label: t.label, group: i.label })),
  ])),
  { to: '/vault', label: 'Vault', group: 'Settings' },
  { to: '/naming', label: 'Naming', group: 'Settings' },
  state.meta?.is_admin && { to: '/admin/general', label: 'Settings', group: '' },
  state.meta?.is_admin && { to: '/admin/users', label: 'Users', group: 'Settings' },
  { to: '/plugins?tab=market', label: 'Plugin Marketplace', group: 'Integrations' },
  state.meta?.is_admin && { to: '/admin/plugins', label: 'Plugin repositories & upload', group: 'Settings' },
  state.meta?.is_admin && { to: '/admin/backup', label: 'Backup & restore', group: 'Settings' },
  { to: '/account', label: 'Password & API tokens', group: 'Account' },
  { to: '/help', label: 'Help', group: '' },
  { to: '/about', label: 'About labdex', group: '' },
].filter(Boolean))

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

// Who is signed in and the meta first, then the rest. The view changes once, when both are known: clearing the
// error before the requests would blank the page in between, and a failed retry would flash.
async function start() {
  retrying.value = true
  let st
  try {
    st = await loadAuth()
    if (st.user) await loadMeta()
  } catch (e) {
    error.value = `Can't reach the labdex API: ${e.message}`
    return
  } finally {
    retrying.value = false
  }
  error.value = ''
  if (st.user) boot()
}

function boot() {
  refreshLint()
  refreshPending()
  loadNav()
  loadUpdate()
  loadSidebar()
  refreshBar()
  startEvents()
}

// The sign-in form waits on this. state.auth only gets the user once the meta is loaded too, so the form stays
// up (busy) until labdex can be shown, and a backend error lands on the form instead of swapping the page out.
async function signedIn() {
  let st
  try {
    st = await api.get('/api/auth/status')
    if (st.user) await loadMeta()
  } catch (e) {
    throw new Error(`Signed in, but labdex didn't load: ${e.message}`, { cause: e })
  }
  if (!st.user) throw new Error("Signed in, but the browser didn't keep the session. Open labdex at the address it expects (http or https), then try again.")
  state.auth = st
  error.value = ''
  boot()
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
  <div v-if="error" class="page"><div class="errbox" role="alert">{{ error }}
    <button class="btn small" :disabled="retrying" @click="start">{{ retrying ? 'Retrying…' : 'Retry' }}</button></div></div>
  <Login v-else-if="state.auth && !state.auth.user" :signed-in="signedIn" @done="start" />
  <div v-else-if="state.meta" class="shell" :class="{ small, drawer }">
    <a href="#main" class="skip">Skip to the page</a>
    <aside class="side">
      <div class="side-head">
        <RouterLink to="/" class="brand" :title="state.nav?.instance_name"><img :src="mark" alt="" width="24" height="24"><span class="wm">labdex</span></RouterLink>
        <span v-if="state.nav?.instance_name && state.nav.instance_name !== 'labdex' && !small" class="inst">{{ state.nav.instance_name }}</span>
        <span class="grow"></span>
        <button class="icon-btn hide-m" :title="small ? 'Expand the sidebar (Ctrl B)' : 'Collapse the sidebar (Ctrl B)'" :aria-label="small ? 'Expand the sidebar' : 'Collapse the sidebar'" @click="small = !small">
          <PanelLeftOpen v-if="small" :size="17" /><PanelLeftClose v-else :size="17" /></button>
      </div>
      <!-- the site every page is about; a native select, styled, so keyboard and screen readers just work -->
      <label v-if="sites.length > 1" class="siteswitch" :title="`Site: ${siteName}`">
        <span class="dot ok" aria-hidden="true"></span>
        <span class="sw-text"><span class="label">Site</span><span class="sw-name">{{ siteName }}</span></span>
        <ChevronsUpDown class="sw-chev" :size="15" aria-hidden="true" />
        <select :value="state.site" aria-label="Site" @change="pickSite($event.target.value)">
          <option value="">All sites</option>
          <option v-for="x in sites" :key="x.code" :value="x.code">{{ x.name }}</option>
        </select>
      </label>
      <nav class="side-scroll" aria-label="Pages">
        <div class="side-links">
          <template v-for="(g, gn) in groups" :key="g.id">
          <section class="grp" :aria-labelledby="g.label ? `grp-${g.id}` : undefined">
            <h2 v-if="g.label" :id="`grp-${g.id}`" class="gh label">{{ g.label }}</h2>
            <div class="gi">
              <RouterLink v-for="n in g.items" :key="n.to" :to="link(n.to)" class="nl" active-class="" exact-active-class=""
                          :class="{ on: active(n) }" :aria-current="active(n) ? 'page' : undefined" :title="n.label">
                <component :is="n.icon" class="ic" :size="17" />
                <span class="lb">{{ n.label }}</span>
                <span v-if="n.count && state.meta?.counts[n.count] != null" class="count">{{ state.meta.counts[n.count] }}</span>
                <span v-if="badge(n)" class="pill" :class="{ info: n.badge === 'pending', warn: n.badge === 'inbox' && !state.lint?.error }"
                      :title="n.badge === 'pending' ? `${badge(n)} plan(s) waiting for review` : `${badge(n)} check(s) to look at`">{{ badge(n) }}</span>
              </RouterLink>
            </div>
          </section>
          <section v-if="gn === 0 && state.sidebar.pins.length" class="grp" aria-labelledby="grp-pins">
            <h2 id="grp-pins" class="gh label pinhead">Pinned <RouterLink to="/account/sidebar" class="edit">Edit</RouterLink></h2>
            <div class="gi">
              <RouterLink v-for="p in state.sidebar.pins" :key="p.to" :to="p.to" class="nl" active-class="" exact-active-class=""
                          :class="{ on: pinActive(p) }" :aria-current="pinActive(p) ? 'page' : undefined" :title="p.label">
                <component :is="PIN_ICON[p.kind] || Star" class="ic" :size="17" />
                <span class="lb" :class="{ mono: ['host', 'network', 'zone'].includes(p.kind) }">{{ p.label }}</span>
                <span v-if="p.kind === 'host' && live[p.key]" class="dot" :class="LIVE_DOT[live[p.key]] || 'unknown'" :title="live[p.key]"></span>
              </RouterLink>
            </div>
          </section>
          </template>
        </div>
      </nav>
      <div class="who">
        <button class="avbtn" :aria-expanded="menu" :aria-label="`Account: ${state.auth.user.display_name}`" @click="menu = !menu">
          <span class="avatar">{{ state.auth.user.display_name.slice(0, 1).toUpperCase() }}</span>
          <span class="lb">{{ state.auth.user.display_name }}</span>
        </button>
        <RouterLink v-if="state.meta.is_admin" to="/admin" class="icon-btn gear" :class="{ on: $route.path.startsWith('/admin') }" title="Settings" aria-label="Settings"><Settings :size="16" /></RouterLink>
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
          <RouterLink to="/account/sidebar">Your sidebar</RouterLink>
          <RouterLink to="/account">Password &amp; API tokens</RouterLink>
          <RouterLink to="/vault">Vault</RouterLink>
          <RouterLink to="/naming">Naming</RouterLink>
          <RouterLink to="/help">Help</RouterLink>
          <RouterLink to="/about">About labdex {{ state.meta.version }}</RouterLink>
          <button v-if="state.auth.user.via === 'session'" @click="logout">Sign out</button>
        </div>
      </div>
    </aside>
    <div class="scrim" @click="drawer = false"></div>
    <div v-if="menu" class="menu-scrim" @click="menu = false"></div>
    <ConfirmHost />
    <CommandPalette ref="palette" :pages="pages" />
    <div class="body">
      <header class="tbar">
        <button class="icon-btn show-m" aria-label="Menu" @click="drawer = true"><Menu :size="18" /></button>
        <nav class="crumbs" aria-label="You are here">
          <template v-for="(c, n) in crumbs" :key="n">
            <ChevronRight v-if="n" class="sep" :size="13" aria-hidden="true" />
            <RouterLink v-if="c.to && n < crumbs.length - 1" :to="c.to">{{ c.label }}</RouterLink>
            <span v-else :aria-current="n === crumbs.length - 1 ? 'page' : undefined" :class="{ last: n === crumbs.length - 1 }">{{ c.label }}</span>
          </template>
        </nav>
        <button v-if="canPin" class="icon-btn pin" :class="{ on: isPinned(here.to) }" :aria-pressed="isPinned(here.to)"
                :title="isPinned(here.to) ? 'Unpin from your sidebar' : 'Pin to your sidebar'" :aria-label="isPinned(here.to) ? 'Unpin' : 'Pin to the sidebar'"
                @click="togglePin(here)"><Star :size="15" /></button>
        <span class="grow"></span>
        <button class="find" :title="`Search and run (${isMac ? '⌘' : 'Ctrl'} K)`" @click="palette?.show()">
          <Search :size="15" /><span class="lb">Type a command or search</span><kbd>{{ isMac ? '⌘' : 'Ctrl' }} K</kbd>
        </button>
        <RouterLink to="/lint" class="icon-btn inbox" :title="inboxCount ? `Inbox: ${inboxCount} to look at` : 'Inbox'" :aria-label="inboxCount ? `Inbox, ${inboxCount} to look at` : 'Inbox'">
          <Inbox :size="18" /><span v-if="inboxCount" class="ping" aria-hidden="true"></span>
        </RouterLink>
      </header>
      <main id="main" class="main" tabindex="-1">
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
        <!-- an area made of several pages (Physical: racks, cables, labels) shows them as tabs -->
        <nav v-if="tabs.length > 1 && !$route.params.name && !$route.params.id" class="areatabs" :aria-label="entry.label">
          <RouterLink v-for="t in tabs" :key="t.to" :to="t.to" active-class="" exact-active-class="" :class="{ on: tabOn(t) }"
                      :aria-current="tabOn(t) ? 'page' : undefined">{{ t.label }}</RouterLink>
        </nav>
        <!-- List pages stay alive: back from a host is instant and keeps scroll, filters and the cached rows.
             They refresh from the query cache when shown again. -->
        <RouterView v-slot="{ Component, route: r }">
          <KeepAlive :include="KEEP" :max="6">
            <component :is="Component" :key="r.path" />
          </KeepAlive>
        </RouterView>
      </main>
      <StatusBar />
    </div>
  </div>

  <div v-if="state.toast" class="toast" :class="state.toast.kind" role="status">
    <AlertTriangle v-if="state.toast.kind === 'error'" :size="16" class="t-ic" /><CheckCircle2 v-else :size="16" class="t-ic" />
    <div class="t-body"><div>{{ state.toast.text }}</div><div v-if="state.toast.sub" class="small faint">{{ state.toast.sub }}</div></div>
    <RouterLink v-if="state.toast.to" :to="state.toast.to.path" class="t-act">{{ state.toast.to.label }}</RouterLink>
    <button class="icon-btn" aria-label="Dismiss" @click="state.toast = null"><X :size="14" /></button>
  </div>
</template>

<style scoped>
.skip { position: absolute; left: 8px; top: -40px; z-index: 100; background: var(--accent); color: var(--on-accent); padding: 6px 12px; border-radius: var(--r-ctl); }
.skip:focus { top: 8px; }
.main:focus { outline: none; }
.themes { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 12px 8px; }
.themes .seg button { padding: 3px 10px; font-size: calc(12px * var(--text)); }

/* the frame: a full-height sidebar with a border, then the top bar, the page on drafting paper, the status bar */
.shell { display: grid; grid-template-columns: 236px minmax(0, 1fr); min-height: 100vh; }
.shell.small { grid-template-columns: 56px minmax(0, 1fr); }
.side { position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column; background: var(--panel);
  border-right: 1px solid var(--line); z-index: 20; }
.body { min-width: 0; display: flex; flex-direction: column; min-height: 100vh; }
.main { flex: 1; min-width: 0; background-color: var(--bg);
  /* the dot grid sits behind the content only, never behind the sidebar */
  background-image: radial-gradient(circle, var(--dot) 1px, transparent 1.2px); background-size: 20px 20px; background-position: 10px 10px; }

.side-head { display: flex; align-items: center; gap: 8px; padding: 14px 12px 10px 16px; }
.brand { display: inline-flex; align-items: center; gap: 10px; font-weight: 600; font-size: calc(16px * var(--text)); color: var(--ink); letter-spacing: -.01em; }
.brand:hover { text-decoration: none; }
.inst { font: calc(11.5px * var(--text)) var(--mono); color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.siteswitch { position: relative; display: flex; align-items: center; gap: 10px; margin: 2px 10px 8px; padding: 6px 30px 6px 12px;
  border: 1px solid var(--line); border-radius: var(--r-ctl); background: var(--card); cursor: pointer; }
.sw-chev { position: absolute; right: 9px; color: var(--faint); }
.siteswitch:hover { border-color: var(--line-strong); }
.siteswitch:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-faint); }
.sw-text { display: flex; flex-direction: column; min-width: 0; line-height: 1.25; }
.sw-text .label { font-size: calc(9.5px * var(--text)); }
.sw-name { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* the real control covers the box, invisible: the box is only its face */
.siteswitch select { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }

.side-scroll { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.side-links { flex: 1; min-height: 0; overflow-y: auto; padding: 0 10px 8px; scrollbar-width: thin; }
.grp + .grp { margin-top: 14px; }
/* a section label, not a control: every link is always in view */
.gh { margin: 0 0 3px; padding: 0 8px; font-size: calc(10.5px * var(--text)); }
.gi { display: flex; flex-direction: column; gap: 1px; }
.nl { position: relative; width: 100%; font: inherit; background: none; border: 0; cursor: pointer; display: flex; align-items: center; gap: 10px;
  color: var(--ink); padding: 5px 8px; border-radius: var(--r-ctl); white-space: nowrap; text-align: left; border: 1px solid transparent; }
.nl:hover { text-decoration: none; background: var(--raise); }
.nl.on { background: var(--accent-faint); border-color: color-mix(in srgb, var(--accent) 30%, transparent); }
.nl.on::before { content: ''; position: absolute; left: -1px; top: 6px; bottom: 6px; width: 2px; border-radius: 2px; background: var(--accent); }
.ic { color: var(--muted); flex: none; }
.nl.on .ic { color: var(--accent-soft); }
.lb { flex: 1; overflow: hidden; text-overflow: ellipsis; }
.pinhead { display: flex; align-items: center; }
.pinhead .edit { margin-left: auto; color: var(--faint); font: inherit; }
.pinhead .edit:hover { color: var(--ink); text-decoration: none; }
.nl .mono { font-family: var(--mono); font-size: calc(13px * var(--text)); }
.pin.on { color: var(--accent-soft); }
.pin.on svg { fill: currentColor; }
.count { color: var(--faint); font: calc(11.5px * var(--text)) var(--mono); font-variant-numeric: tabular-nums; }

.who { position: relative; display: flex; align-items: center; gap: 4px; padding: 10px 10px 12px; border-top: 1px solid var(--line); }
.avbtn { flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px; font: inherit; color: var(--ink); background: none; border: 0; padding: 4px 6px;
  border-radius: var(--r-ctl); cursor: pointer; text-align: left; }
.avbtn:hover, .avbtn[aria-expanded="true"] { background: var(--raise); }
.avatar { width: 24px; height: 24px; border-radius: 50%; background: var(--ink); color: var(--bg); display: inline-grid; place-items: center;
  font-size: calc(11px * var(--text)); font-weight: 600; flex: none; }
.gear.on { color: var(--accent-soft); }
.menu { position: absolute; left: 10px; bottom: 56px; z-index: 30; min-width: 250px; }
.mhead { padding: 8px 12px 6px; border-bottom: 1px solid var(--line); margin-bottom: 4px; }
.menu-scrim { position: fixed; inset: 0; z-index: 19; }
.menu a, .menu button:not(.seg button) { display: block; width: 100%; text-align: left; font: inherit; color: var(--ink); background: none; border: 0;
  padding: 7px 12px; border-radius: var(--r-ctl); cursor: pointer; }
.menu a:hover, .menu > button:hover { background: var(--raise); text-decoration: none; }

.tbar { display: flex; align-items: center; gap: 10px; padding: 0 16px 0 22px; height: 50px; border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 12; background: var(--bar-bg); backdrop-filter: blur(6px); }
.crumbs { display: flex; align-items: center; gap: 6px; min-width: 0; color: var(--muted); font-size: calc(13.5px * var(--text)); white-space: nowrap; overflow: hidden; }
.crumbs a { color: var(--muted); }
.crumbs a:hover { color: var(--ink); }
.crumbs .last { color: var(--ink); font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
.crumbs .sep { color: var(--faint); flex: none; }
.find { display: flex; align-items: center; gap: 8px; width: min(380px, 34vw); padding: 5px 6px 5px 10px; border: 1px solid var(--line); border-radius: var(--r-ctl);
  background: var(--card); color: var(--faint); font: inherit; font-size: calc(13px * var(--text)); cursor: pointer; }
.find:hover { color: var(--ink); border-color: var(--line-strong); }
.find .lb { text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.find kbd { font: calc(11px * var(--text)) var(--mono); color: var(--faint); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 0 5px; background: var(--bg); }
.inbox { position: relative; }
.ping { position: absolute; top: 3px; right: 3px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 2px var(--bar-bg); }

.areatabs { display: flex; gap: 4px; padding: 14px 22px 0; overflow-x: auto; scrollbar-width: none; }
.areatabs a { padding: 4px 12px; border-radius: var(--r-chip); border: 1px solid var(--line); background: var(--card); color: var(--muted);
  font-size: calc(13px * var(--text)); font-weight: 500; white-space: nowrap; }
.areatabs a:hover { color: var(--ink); text-decoration: none; border-color: var(--line-strong); }
.areatabs a.on { color: var(--ink); border-color: var(--accent); background: var(--accent-faint); }

.icon-btn { font: inherit; background: none; border: 1px solid transparent; color: var(--muted); cursor: pointer; border-radius: var(--r-ctl); padding: 5px; display: inline-grid; place-items: center; }
.icon-btn:hover { color: var(--ink); background: var(--raise); text-decoration: none; }
.show-m { display: none !important; }

.small .lb, .small .count, .small .pill, .small .wm, .small .gh, .small .gear, .small .nl .dot { display: none; }
.small .grp + .grp { margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--line); }
.small .side-head { padding: 12px 8px 8px; flex-direction: column; }
.small .side-links { padding: 0 6px 8px; }
.small .nl, .small .avbtn { justify-content: center; }
.small .who { justify-content: center; padding: 10px 6px; }
.small .siteswitch { display: none; }
.scrim { display: none; }

/* toasts sit bottom right, with a coloured edge that says what happened */
.toast { position: fixed; bottom: 40px; right: 20px; z-index: 50; display: flex; align-items: flex-start; gap: 10px; width: min(92vw, 420px);
  background: var(--card); color: var(--ink); border: 1px solid var(--line); border-left: 3px solid var(--ok); border-radius: var(--r-box);
  padding: 10px 10px 10px 14px; box-shadow: 0 10px 28px var(--shadow); }
.toast.error { border-left-color: var(--err); }
.toast.info { border-left-color: var(--info); }
.toast .t-ic { flex: none; margin-top: 2px; color: var(--ok); }
.toast.error .t-ic { color: var(--err); }
.toast .t-body { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.toast .t-act { font-weight: 500; white-space: nowrap; }
@media (max-width: 860px) {
  .shell, .shell.small { grid-template-columns: minmax(0, 1fr); }
  .side { position: fixed; left: 0; top: 0; width: 272px; transform: translateX(-100%); transition: transform .18s; }
  .drawer .side { transform: none; }
  .drawer .scrim { display: block; position: fixed; inset: 0; background: var(--scrim); z-index: 15; }
  .small .lb, .small .count, .small .pill, .small .wm, .small .gh, .small .gear { display: initial; }
  .small .siteswitch { display: flex; }
  .tbar { padding: 0 12px; }
  .find { width: auto; }
  .find .lb, .find kbd { display: none; }
  .show-m { display: revert !important; }
  .areatabs { padding: 12px 16px 0; }
}
</style>
