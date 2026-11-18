<script setup>
// Your sidebar: what you pinned, which pages show and in which order. Only for you; hidden pages
// stay one Ctrl K away. Moving uses buttons (keyboard and screen readers work the same).
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowDown, ArrowUp, Star } from 'lucide-vue-next'
import { SECTIONS } from '../nav'
import { api } from '../api'
import { saveSidebar, state, toast } from '../store'

const router = useRouter()
const pins = computed(() => state.sidebar.pins)
const shows = (what) => !what || Boolean(state.nav?.show[what])
const rank = (to) => { const n = state.sidebar.order.indexOf(to); return n < 0 ? 1000 : n }
const groups = computed(() => SECTIONS.map((g) => ({
  ...g, items: g.items.filter((i) => shows(i.show)).map((i, n) => ({ i, n }))
    .sort((a, b) => rank(a.i.to) - rank(b.i.to) || a.n - b.n).map((x) => x.i),
})))

function movePin(n, d) {
  const list = [...pins.value]
  const [x] = list.splice(n, 1)
  list.splice(n + d, 0, x)
  saveSidebar({ pins: list })
}
const unpin = (p) => saveSidebar({ pins: pins.value.filter((x) => x !== p) })
function movePage(g, n, d) {
  const list = g.items.map((i) => i.to)
  const [x] = list.splice(n, 1)
  list.splice(n + d, 0, x)
  // one order list for every group: this group's pages in their new order, the rest as they were
  const rest = state.sidebar.order.filter((to) => !list.includes(to))
  saveSidebar({ order: [...rest, ...list] })
}
const hidden = (to) => state.sidebar.hidden.includes(to)
const flip = (to) => saveSidebar({ hidden: hidden(to) ? state.sidebar.hidden.filter((x) => x !== to) : [...state.sidebar.hidden, to] })
// back to the default pages (an administrator's, or labdex's own); pins stay
async function reset() {
  try { state.sidebar = await api.del('/api/me/sidebar') } catch (e) { toast(e.message, 'error') }
}
// administrators: these pages, hidden and in this order, for everyone who hasn't changed theirs
async function makeDefault() {
  try {
    await api.put('/api/admin/sidebar-default', { hidden: state.sidebar.hidden, order: state.sidebar.order })
    toast('These pages are now the default for everyone who hasn’t changed theirs')
  } catch (e) { toast(e.message, 'error') }
}
const KIND = { host: 'host', network: 'network', zone: 'zone', service: 'service', change: 'change', view: 'saved view', page: 'page' }
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Your sidebar</h1><span class="h-tag">{{ state.sidebar.from_default ? 'pages: the default' : 'only for you' }}</span>
      <span class="grow"></span>
      <button v-if="state.meta?.is_admin" class="btn" title="Hidden pages and order for everyone who hasn't changed their own" @click="makeDefault">Make these pages the default</button>
      <button class="btn" :disabled="state.sidebar.from_default" @click="reset">Use the default pages</button>
      <button class="btn primary" @click="router.back()">Done</button>
    </div>
    <div class="split">
      <section class="card flush">
        <div class="sect-head pad"><h2>Pinned</h2><span class="grow"></span>
          <span class="note">Pin anything with the <Star :size="12" /> in the top bar</span></div>
        <ul v-if="pins.length" class="plist">
          <li v-for="(p, n) in pins" :key="p.to">
            <span class="grow"><RouterLink :to="p.to" class="mono">{{ p.label }}</RouterLink> <span class="small faint">{{ KIND[p.kind] }}</span></span>
            <button class="btn icon" :disabled="n === 0" :aria-label="`Move ${p.label} up`" @click="movePin(n, -1)"><ArrowUp :size="15" /></button>
            <button class="btn icon" :disabled="n === pins.length - 1" :aria-label="`Move ${p.label} down`" @click="movePin(n, 1)"><ArrowDown :size="15" /></button>
            <button class="btn small ghost" @click="unpin(p)">Unpin</button>
          </li>
        </ul>
        <p v-else class="empty-line pad">Nothing pinned yet. Open a host, network, zone or a filtered list and press the star.</p>
        <p class="small faint pad m-0">Hosts, networks, zones, changes, pages and filtered lists can be pinned. Pinned hosts show their live dot.</p>
      </section>
      <section class="card flush">
        <div class="sect-head pad"><h2>Pages</h2></div>
        <div v-for="g in groups" :key="g.id" class="pgrp">
          <div v-if="g.label" class="label pad-x">{{ g.label }}</div>
          <ul class="plist">
            <li v-for="(i, n) in g.items" :key="i.to" :class="{ off: hidden(i.to) }">
              <component :is="i.icon" :size="16" class="faint" />
              <span class="grow">{{ i.label }}</span>
              <button class="btn icon" :disabled="n === 0" :aria-label="`Move ${i.label} up`" @click="movePage(g, n, -1)"><ArrowUp :size="15" /></button>
              <button class="btn icon" :disabled="n === g.items.length - 1" :aria-label="`Move ${i.label} down`" @click="movePage(g, n, 1)"><ArrowDown :size="15" /></button>
              <label class="check" :title="hidden(i.to) ? 'Hidden: still in Ctrl K' : 'Shown'">
                <input type="checkbox" :checked="!hidden(i.to)" :aria-label="`Show ${i.label} in the sidebar`" @change="flip(i.to)"></label>
            </li>
          </ul>
        </div>
        <p class="small faint pad m-0">Hidden pages stay one Ctrl K away.</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.pad { padding: 14px 18px; margin: 0; }
.pad-x { padding: 10px 18px 4px; }
.plist { list-style: none; margin: 0; padding: 0; }
.plist li { display: flex; align-items: center; gap: 8px; padding: 7px 14px 7px 18px; border-top: 1px solid var(--line); }
.plist li.off .grow { color: var(--faint); }
.pgrp + .pgrp { margin-top: 6px; }
.split > .card { margin-top: 0; }
</style>
