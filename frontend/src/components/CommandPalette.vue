<script setup>
// Ctrl/⌘ K (or /): one box for everything. Hosts, addresses, MACs, networks, services and
// aliases come from /api/search; pages and "new …" are local; integration actions run here.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  AppWindow, ArrowRight, CornerDownLeft, FilePlus2, ListTree, Network, Palette, Play, Plug, Search, Server, Tags,
} from 'lucide-vue-next'
import { api } from '../api'
import { confirm } from '../confirm'
import { can, toast } from '../store'
import { setTextSize, setTheme } from '../theme'

const props = defineProps({ pages: { type: Array, default: () => [] } })
const router = useRouter()
const open = ref(false)
const q = ref('')
const found = ref([])
const active = ref(0)
const busy = ref(false)
const input = ref(null)
const list = ref(null)
let timer = null
let seq = 0

const ICON = { filter: Tags, theme: Palette, host: Server, network: Network, service: AppWindow, alias: ListTree, integration: Plug, action: Play, page: ArrowRight, new: FilePlus2 }
const KIND = { filter: 'Filter', theme: 'Appearance', host: 'Hosts', network: 'Networks', service: 'Services', alias: 'Aliases', integration: 'Integrations', action: 'Run', page: 'Go to', new: 'Create' }

const creates = computed(() => [
  can('host', 'edit') && { kind: 'new', title: 'New host', link: '/hosts/new', words: 'add create host server vm' },
  can('host', 'edit') && { kind: 'new', title: 'New VIP', link: '/hosts/new?kind=vip', words: 'add create vip shared address' },
  can('network', 'edit') && { kind: 'new', title: 'New network', link: '/networks/new', words: 'add create network vlan subnet' },
  { kind: 'theme', title: 'Light theme', set: 'light', words: 'theme light bright day appearance' },
  { kind: 'theme', title: 'Dark theme', set: 'dark', words: 'theme dark night appearance' },
  { kind: 'theme', title: 'Theme like the system', set: 'system', words: 'theme auto system appearance' },
  { kind: 'theme', title: 'Compact text', size: 'compact', words: 'text size font small compact appearance' },
  { kind: 'theme', title: 'Comfortable text', size: 'comfortable', words: 'text size font normal medium comfortable appearance' },
  { kind: 'theme', title: 'Large text', size: 'large', words: 'text size font big large bigger appearance' },
].filter(Boolean))

const local = computed(() => {
  const n = q.value.trim().toLowerCase()
  const pages = props.pages.map((p) => ({ kind: 'page', title: p.label, link: p.to, sub: p.group }))
  if (!n) return [...pages.slice(0, 8), ...creates.value.filter((x) => x.kind === 'new')]
  const hit = (x) => `${x.title} ${x.sub || ''} ${x.words || ''}`.toLowerCase().includes(n)
  return [...pages.filter(hit), ...creates.value.filter(hit)]
})

const results = computed(() => {
  const remote = found.value
  const pages = local.value.filter((p) => p.kind === 'page')
  const news = local.value.filter((p) => p.kind === 'new' || p.kind === 'theme')
  // a MAC or an address: also "where is it plugged in?"
  const t = q.value.trim()
  const where = /^([0-9a-f]{2}[:-]?){5}[0-9a-f]{2}$|^[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}$|^\d{1,3}(\.\d{1,3}){3}$/i.test(t)
    ? [{ kind: 'page', title: `Where is ${t}?`, link: `/discovery?tab=where&q=${encodeURIComponent(t)}`, sub: 'switch port from the MAC tables' }] : []
  // inventory first, pages after (unless nothing else matched), creating last
  return [...remote, ...where, ...pages, ...news]
})
const groups = computed(() => {
  const out = []
  results.value.forEach((r, i) => {
    const label = KIND[r.kind]
    if (!out.length || out[out.length - 1].label !== label) out.push({ label, items: [] })
    out[out.length - 1].items.push({ ...r, i })
  })
  return out
})

watch(q, (v) => {
  active.value = 0
  clearTimeout(timer)
  const n = v.trim()
  if (n.length < 2) { found.value = []; return }
  timer = setTimeout(async () => {
    const mine = ++seq
    try {
      const d = await api.get(`/api/search?q=${encodeURIComponent(n)}`)
      if (mine === seq) found.value = d.results
    } catch { if (mine === seq) found.value = [] }
  }, 120)
})

function show() {
  open.value = true
  q.value = ''
  found.value = []
  active.value = 0
  nextTick(() => input.value?.focus())
}
function hide() { open.value = false }

async function pick(r) {
  if (!r) return
  if (r.kind === 'theme') { if (r.size) setTextSize(r.size); else setTheme(r.set); hide(); return }
  if (r.kind === 'action') {
    if (r.danger && !(await confirm({ title: `${r.title}?`, body: `On ${r.integration}. This changes things right away, without a plan.`, action: r.title, danger: true }))) return
    busy.value = true
    try {
      const job = await api.post(`/api/integrations/${encodeURIComponent(r.integration)}/actions/${encodeURIComponent(r.action)}`)
      hide()
      router.push(`/jobs/${job.id}`)
    } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
    return
  }
  hide()
  router.push(r.link)
}

function key(e) {
  const n = results.value.length
  if (e.key === 'ArrowDown') { e.preventDefault(); active.value = n ? (active.value + 1) % n : 0 }
  else if (e.key === 'ArrowUp') { e.preventDefault(); active.value = n ? (active.value - 1 + n) % n : 0 }
  else if (e.key === 'Enter') { e.preventDefault(); pick(results.value[active.value]) }
  else if (e.key === 'Escape') { e.preventDefault(); hide() }
}
watch(active, () => nextTick(() => list.value?.querySelector('.on')?.scrollIntoView({ block: 'nearest' })))

function global(e) {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName) || e.target?.isContentEditable
  if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) { e.preventDefault(); open.value ? hide() : show() }
  else if (e.key === '/' && !typing && !open.value && !document.querySelector('.dlg-back')) { e.preventDefault(); show() }
}
onMounted(() => window.addEventListener('keydown', global))
onBeforeUnmount(() => window.removeEventListener('keydown', global))
defineExpose({ show })
const mac = navigator.platform?.toLowerCase().includes('mac')
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="pal-back" @mousedown.self="hide">
      <div class="pal" role="dialog" aria-modal="true" aria-label="Search and go">
        <div class="pal-in">
          <Search :size="17" class="faint" />
          <input ref="input" v-model="q" type="text" placeholder="Hosts, addresses, MACs, networks, services, pages…" aria-label="Search"
                 role="combobox" :aria-expanded="results.length > 0" aria-controls="pal-list"
                 :aria-activedescendant="results.length ? `pal-${active}` : undefined" autocomplete="off" spellcheck="false" @keydown="key">
          <kbd>Esc</kbd>
        </div>
        <div id="pal-list" ref="list" class="pal-list" role="listbox">
          <template v-for="g in groups" :key="g.label">
            <div class="pal-g">{{ g.label }}</div>
            <div v-for="r in g.items" :id="`pal-${r.i}`" :key="r.i" class="pal-r" :class="{ on: r.i === active }" role="option"
                 :aria-selected="r.i === active" @mousemove="active = r.i" @click="pick(r)">
              <component :is="ICON[r.kind]" :size="15" class="pal-ic" />
              <span class="pal-t" :class="{ mono: r.kind === 'host' || r.kind === 'alias' }">{{ r.title }}</span>
              <span v-if="r.fun" class="faint small">{{ r.fun }}</span>
              <span class="pal-s">{{ r.sub }}</span>
              <CornerDownLeft v-if="r.i === active" :size="14" class="faint" />
            </div>
          </template>
          <div v-if="q.trim().length >= 2 && !results.length" class="pal-none">Nothing matches “{{ q.trim() }}”.</div>
        </div>
        <div class="pal-foot small faint">
          <span><kbd>↑</kbd><kbd>↓</kbd> choose</span><span><kbd>↵</kbd> open</span>
          <span><kbd>{{ mac ? '⌘' : 'Ctrl' }}</kbd><kbd>K</kbd> or <kbd>/</kbd> anywhere</span>
          <span v-if="busy">starting…</span>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.pal-back { position: fixed; inset: 0; background: var(--scrim); z-index: 60; display: grid; place-items: start center; padding: 12vh 16px 16px; }
.pal { width: min(640px, 100%); background: var(--card); border: 1px solid var(--line-strong); border-radius: var(--r-box);
  box-shadow: 0 24px 60px var(--shadow); overflow: hidden; display: flex; flex-direction: column; max-height: 70vh; }
.pal-in { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--line); }
.pal-in input { border: 0 !important; background: none !important; padding: 0 !important; font-size: calc(15px * var(--text)); box-shadow: none !important; flex: 1; }
.pal-list { overflow-y: auto; padding: 6px; }
.pal-g { font-size: calc(12px * var(--text)); color: var(--faint); padding: 8px 10px 4px; }
.pal-r { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: var(--r-ctl); cursor: pointer; min-width: 0; }
.pal-r.on { background: var(--raise); }
.pal-ic { color: var(--muted); flex: none; }
.pal-t { font-weight: 500; white-space: nowrap; }
.pal-s { color: var(--muted); font-size: calc(12.5px * var(--text)); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pal-none { padding: 18px 12px; color: var(--faint); }
.pal-foot { display: flex; gap: 16px; padding: 8px 14px; border-top: 1px solid var(--line); background: var(--panel); flex-wrap: wrap; }
kbd { font-family: var(--mono); font-size: calc(11px * var(--text)); border: 1px solid var(--line-strong); border-bottom-width: 2px; border-radius: var(--r-sm); padding: 0 5px; margin-right: 3px; color: var(--muted); }
</style>
