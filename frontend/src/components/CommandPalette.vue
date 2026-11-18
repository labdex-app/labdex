<script setup>
// Ctrl/⌘ K (or /): one box for everything. Hosts, addresses, MACs, networks, services and
// aliases come from /api/search; pages and "new …" are local; integration actions run here.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  AppWindow, ArrowRight, FilePlus2, Layers, ListTree, Network, Palette, Pencil, Play, Plug, Search, Server, Tags, Unplug,
} from 'lucide-vue-next'
import { api } from '../api'
import { confirm } from '../confirm'
import { can, state, toast } from '../store'
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

const ICON = { filter: Tags, theme: Palette, host: Server, network: Network, zone: Layers, service: AppWindow, alias: ListTree, integration: Plug, action: Play, edit: Pencil, impact: Unplug, page: ArrowRight, new: FilePlus2 }
const KIND = { filter: 'Filter', theme: 'Appearance', host: 'Hosts', network: 'Networks', zone: 'Zones', service: 'Services', alias: 'Aliases', integration: 'Integrations', action: 'Actions', edit: 'Actions', impact: 'Actions', page: 'Pages', new: 'Create' }
// "> …" keeps only commands: actions, creating, appearance
const commandsOnly = computed(() => q.value.trimStart().startsWith('>'))
const term = computed(() => q.value.trim().replace(/^>\s*/, ''))

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
  const n = term.value.toLowerCase()
  const pages = props.pages.map((p) => ({ kind: 'page', title: p.label, link: p.to, sub: p.group }))
  if (!n) return [...pages.slice(0, 8), ...creates.value.filter((x) => x.kind === 'new')]
  const hit = (x) => `${x.title} ${x.sub || ''} ${x.words || ''}`.toLowerCase().includes(n)
  return [...pages.filter(hit), ...creates.value.filter(hit)]
})

const results = computed(() => {
  const remote = found.value
  const pages = local.value.filter((p) => p.kind === 'page')
  const news = local.value.filter((p) => p.kind === 'new' || p.kind === 'theme')
  const actions = remote.filter((r) => r.kind === 'action')
  if (commandsOnly.value) return [...actions, ...news]
  const t = term.value
  const where = /^([0-9a-f]{2}[:-]?){5}[0-9a-f]{2}$|^[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}$|^\d{1,3}(\.\d{1,3}){3}$/i.test(t)
    ? [{ kind: 'page', title: `Where is ${t}?`, link: `/discovery?tab=where&q=${encodeURIComponent(t)}`, sub: 'switch port from the MAC tables' }] : []
  // the best host hit: "what breaks if it goes down?"
  const top = remote.find((r) => r.kind === 'host')
  const uid = top?.link.split('/').pop()
  const forTop = top ? [
    ...(can('host', 'edit') ? [{ kind: 'edit', title: `Edit ${top.title}`, target: top.title, link: `/hosts?peek=${uid}&edit=1`, sub: 'quick edit beside the list' }] : []),
    { kind: 'impact', title: `What breaks if ${top.title} goes down?`, target: top.title, link: `/impact?host=${encodeURIComponent(top.title)}`, sub: 'impact' },
  ] : []
  // actions first ("patch web-01": what you came to do), then the inventory with the quick
  // actions for the best host, pages, creating last. A plain name still opens on Enter.
  const rest = remote.filter((r) => r.kind !== 'action')
  const hostsEnd = rest.map((r) => r.kind).lastIndexOf('host') + 1
  return [...actions, ...rest.slice(0, hostsEnd), ...forTop, ...rest.slice(hostsEnd), ...where, ...pages, ...news]
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

watch(term, (v) => {
  active.value = 0
  clearTimeout(timer)
  const n = v
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
    // a plan only plans: it waits on Changes for review and approval; a run starts right away
    if (r.mode !== 'plan' && r.danger && !(await confirm({ title: `${r.title}?`, body: `On ${r.integration}. This changes things right away, without a plan.`, action: r.title, danger: true }))) return
    busy.value = true
    try {
      const enc = encodeURIComponent
      const job = r.host
        ? await api.post(`/api/hosts/${enc(r.host)}/modules/${enc(r.integration)}/actions/${enc(r.action)}`, {})
        : await api.post(`/api/integrations/${enc(r.integration)}/actions/${enc(r.action)}`, r.params ? { params: r.params } : undefined)
      hide()
      router.push(r.mode === 'plan' ? `/changes?job=${job.id}` : `/jobs/${job.id}`)
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
          <input ref="input" v-model="q" type="text" placeholder="Search, or a verb and a host: patch web-01" aria-label="Search"
                 role="combobox" :aria-expanded="results.length > 0" aria-controls="pal-list"
                 :aria-activedescendant="results.length ? `pal-${active}` : undefined" autocomplete="off" spellcheck="false" @keydown="key">
          <span class="h-tag">site <b>{{ state.site || 'all' }}</b></span>
        </div>
        <div id="pal-list" ref="list" class="pal-list" role="listbox">
          <template v-for="g in groups" :key="g.label">
            <div class="pal-g">{{ g.label }}</div>
            <div v-for="r in g.items" :id="`pal-${r.i}`" :key="r.i" class="pal-r" :class="{ on: r.i === active }" role="option"
                 :aria-selected="r.i === active" @mousemove="active = r.i" @click="pick(r)">
              <component :is="ICON[r.kind]" :size="15" class="pal-ic" />
              <span class="pal-main">
                <span v-if="r.target && r.title.includes(r.target)" class="pal-t">{{ r.title.slice(0, r.title.lastIndexOf(r.target)) }}<b class="mono">{{ r.target }}</b>{{ r.title.slice(r.title.lastIndexOf(r.target) + r.target.length) }}</span>
                <span v-else class="pal-t" :class="{ mono: r.kind === 'host' || r.kind === 'alias' || r.kind === 'zone' }">{{ r.title }}<span v-if="r.fun" class="faint small pal-fun">{{ r.fun }}</span></span>
                <span v-if="r.sub" class="pal-s">{{ r.sub }}</span>
              </span>
              <kbd v-if="r.i === active" class="pal-enter">Enter</kbd>
            </div>
          </template>
          <div v-if="q.trim().length >= 2 && !results.length" class="pal-none">Nothing matches “{{ q.trim() }}”.</div>
        </div>
        <div class="pal-foot small faint">
          <span><kbd>↑↓</kbd> move</span><span><kbd>Enter</kbd> run</span>
          <span><kbd>&gt;</kbd> commands only</span>
          <span v-if="busy">starting…</span>
          <span class="grow"></span>
          <span><kbd>{{ mac ? '⌘' : 'Ctrl' }} K</kbd> or <kbd>/</kbd> anywhere · <kbd>Esc</kbd> to close</span>
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
.pal-g { font: 500 calc(11px * var(--text)) var(--mono); text-transform: uppercase; letter-spacing: .06em; color: var(--faint); padding: 10px 10px 4px; }
.pal-r { display: flex; align-items: center; gap: 12px; padding: 8px 10px; border-radius: var(--r-ctl); cursor: pointer; min-width: 0; }
.pal-r.on { background: var(--accent-faint); }
.pal-r.on .pal-ic { color: var(--accent-soft); }
.pal-ic { color: var(--muted); flex: none; }
.pal-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.pal-t { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pal-t b { font-weight: 600; }
.pal-fun { margin-left: 8px; font-family: var(--sans); }
.pal-s { color: var(--muted); font-size: calc(12.5px * var(--text)); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pal-enter { flex: none; }
.pal-none { padding: 18px 12px; color: var(--faint); }
.pal-foot { display: flex; gap: 16px; padding: 8px 14px; border-top: 1px solid var(--line); background: var(--panel); flex-wrap: wrap; }
kbd { font-family: var(--mono); font-size: calc(11px * var(--text)); border: 1px solid var(--line-strong); border-bottom-width: 2px; border-radius: var(--r-sm); padding: 0 5px; margin-right: 3px; color: var(--muted); }
</style>
