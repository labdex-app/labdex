<script setup>
// Dashboards: cards on a 12-column grid. "Overview" is the shared start page; anyone can make
// dashboards of their own (a page of links, a wall for the rack room), administrators shared ones.
// Edit: drag a card by its handle, resize it from the corner, add and remove cards; Done saves.
import { GripVertical, LayoutGrid, Pencil, Plus, Settings2, Trash2, X } from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { confirm } from '../confirm'
import DashItem from '../components/DashItem.vue'
import Field from '../components/Field.vue'
import Modal from '../components/Modal.vue'
import { useQuery } from '../query'
import { state, toast } from '../store'

const route = useRoute()
const router = useRouter()
const COLS = 12
const ROW = 36 // px per grid row
const GAP = 14

const listQ = useQuery('/api/dashboards', { staleMs: 30000, onError: (e) => toast(e.message, 'error') })
const list = computed(() => listQ.data.value?.dashboards || [])
const types = computed(() => listQ.data.value?.types || {})
const current = computed(() => list.value.find((x) => x.uid === route.query.d) || list.value[0] || null)
const dashQ = useQuery(() => (current.value ? `/api/dashboards/${current.value.uid}` : null), { staleMs: 10000 })
const dash = computed(() => dashQ.data.value)
const dataQ = useQuery('/api/dashboard', { staleMs: 10000, onError: (e) => toast(e.message, 'error') })
const d = computed(() => dataQ.data.value)
const widgetsQ = useQuery('/api/widgets', { staleMs: 30000 })

// ------------------------------------------------------------------ editing
const editing = ref(false)
const work = ref([]) // the cards while editing
const items = computed(() => (editing.value ? work.value : dash.value?.items || []))
const busy = ref(false)
function startEdit() { work.value = JSON.parse(JSON.stringify(dash.value.items)); editing.value = true }
function cancelEdit() { editing.value = false; work.value = [] }
async function saveEdit() {
  busy.value = true
  try {
    const saved = await api.put(`/api/dashboards/${dash.value.uid}`, { items: work.value })
    dashQ.data.value = saved
    editing.value = false
    toast('Dashboard saved')
  } catch (e) {
    const first = e.errors?.[0]
    const n = first && /^items\[(\d+)\]/.exec(first.field)
    const card = n && work.value[Number(n[1])]
    toast(first ? `${card ? `${card.config?.title || types.value[card.type]}: ` : ''}${first.message}` : e.message, 'error')
  } finally { busy.value = false }
}

// cards never overlap: the one you move wins, the others make room below it, then everything floats up
const hits = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
function settle(moved) {
  const placed = moved ? [moved] : []
  for (const it of [...work.value].filter((x) => x !== moved).sort((a, b) => a.y - b.y || a.x - b.x)) {
    let block
    while ((block = placed.find((p) => hits(p, it)))) it.y = block.y + block.h
    placed.push(it)
  }
  for (const it of [...work.value].sort((a, b) => a.y - b.y || a.x - b.x)) {
    if (it === moved) continue
    while (it.y > 0 && !work.value.some((o) => o !== it && hits(o, { ...it, y: it.y - 1 }))) it.y--
  }
}

const gridEl = ref(null)
let drag = null
function pointerDown(e, it, mode) {
  if (!editing.value || !gridEl.value) return
  e.preventDefault()
  const r = gridEl.value.getBoundingClientRect()
  drag = { it, mode, sx: e.clientX, sy: e.clientY, x: it.x, y: it.y, w: it.w, h: it.h, colW: (r.width + GAP) / COLS }
  window.addEventListener('pointermove', pointerMove)
  window.addEventListener('pointerup', pointerUp, { once: true })
}
function pointerMove(e) {
  if (!drag) return
  const dc = Math.round((e.clientX - drag.sx) / drag.colW)
  const dr = Math.round((e.clientY - drag.sy) / (ROW + GAP))
  const it = drag.it
  if (drag.mode === 'move') {
    it.x = Math.max(0, Math.min(COLS - it.w, drag.x + dc))
    it.y = Math.max(0, drag.y + dr)
  } else {
    it.w = Math.max(2, Math.min(COLS - it.x, drag.w + dc))
    it.h = Math.max(2, Math.min(40, drag.h + dr))
  }
  settle(it)
}
function pointerUp() { drag = null; window.removeEventListener('pointermove', pointerMove) }
onBeforeUnmount(() => window.removeEventListener('pointermove', pointerMove))
// keyboard: with a card's handle focused, arrows move it, shift+arrows resize it
function keyMove(e, it) {
  const k = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key]
  if (!k) return
  e.preventDefault()
  if (e.shiftKey) { it.w = Math.max(2, Math.min(COLS - it.x, it.w + k[0])); it.h = Math.max(2, it.h + k[1]) }
  else { it.x = Math.max(0, Math.min(COLS - it.w, it.x + k[0])); it.y = Math.max(0, it.y + k[1]) }
  settle(it)
}
const place = (it) => ({ gridColumn: `${it.x + 1} / span ${it.w}`, gridRow: `${it.y + 1} / span ${it.h}` })
const rows = computed(() => Math.max(1, ...items.value.map((it) => it.y + it.h)) + (editing.value ? 4 : 0))

// ------------------------------------------------------------------ cards
const SIZE = { counts: [12, 3], widgets: [12, 6], widget: [4, 4], links: [6, 4], services: [6, 5], note: [4, 3] }
const addOpen = ref(false)
const cfgDlg = ref(null) // { it, cfg } a card's settings
function addCard(type, extra = {}) {
  const [w, h] = SIZE[type] || [6, 5]
  work.value.push({ id: Math.random().toString(36).slice(2, 10), type, x: 0, y: rows.value, w, h, config: { ...extra } })
  const it = work.value.at(-1) // the reactive one: settle() compares cards by identity
  settle(it)
  addOpen.value = false
  if (['links', 'services', 'note'].includes(type)) openCfg(it)
}
function removeCard(it) { work.value = work.value.filter((x) => x !== it); settle(null) }
function openCfg(it) {
  const c = JSON.parse(JSON.stringify(it.config || {}))
  if (it.type === 'links') c.links = [...(c.links || []), { name: '', url: '', description: '' }]
  cfgDlg.value = { it, cfg: c }
}
// the same rule as the server: http(s) addresses, or a page of labdex
const safeUrl = (u) => !/[\\\s]/.test(u) && ((u.startsWith('/') && !u.startsWith('//')) || /^https?:\/\/[^/]+/i.test(u))
const badLink = computed(() => (cfgDlg.value?.cfg.links || []).find((l) => (l.name || l.url) && !safeUrl(l.url || '')))
function saveCfg() {
  const { it, cfg } = cfgDlg.value
  if (badLink.value) return
  if (cfg.links) cfg.links = cfg.links.filter((l) => l.name || l.url)
  it.config = cfg
  cfgDlg.value = null
}
const plugins = computed(() => (widgetsQ.data.value || []).map((w) => ({ integration: w.integration, widget: w.id, label: w.label })))
const tags = computed(() => (state.meta?.tags || []).map((t) => t.name))

// ------------------------------------------------------------- dashboards
const newDlg = ref(null)
const canShare = computed(() => !!listQ.data.value?.can_share)
function openNew() { newDlg.value = { name: '', shared: false, start: 'links' } }
async function createDash() {
  const n = newDlg.value
  const start = {
    empty: [],
    copy: (dash.value?.items || []).map((x) => ({ ...x })),
    links: [{ type: 'services', x: 0, y: 0, w: 12, h: 6, config: { title: 'Services' } },
            { type: 'links', x: 0, y: 6, w: 6, h: 4, config: { title: 'Links', links: [] } },
            { type: 'note', x: 6, y: 6, w: 6, h: 4, config: { title: 'Notes', text: '' } }],
  }[n.start]
  busy.value = true
  try {
    const made = await api.post('/api/dashboards', { name: n.name, shared: n.shared, items: start })
    newDlg.value = null
    await listQ.refresh()
    router.replace({ query: { d: made.uid } })
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
function renameDash() { newDlg.value = { rename: true, name: dash.value.name } }
async function doRename() {
  busy.value = true
  try {
    dashQ.data.value = await api.put(`/api/dashboards/${dash.value.uid}`, { name: newDlg.value.name })
    newDlg.value = null
    listQ.refresh()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
async function deleteDash() {
  if (!(await confirm({ title: `Delete the dashboard “${dash.value.name}”?`, body: dash.value.shared ? 'It’s shared: it goes for everyone.' : 'Only yours.', action: 'Delete', danger: true }))) return
  await api.del(`/api/dashboards/${dash.value.uid}`)
  editing.value = false
  await listQ.refresh()
  router.replace({ query: {} })
}
watch(() => route.query.d, () => { editing.value = false })
</script>

<template>
  <div class="page dashpage">
    <div class="page-head">
      <h1>{{ state.nav?.instance_name && state.nav.instance_name !== 'labdex' ? state.nav.instance_name : 'Dashboard' }}</h1>
      <span class="sub">labdex {{ state.meta.version }}</span>
      <span class="grow"></span>
      <template v-if="dash?.can_edit">
        <template v-if="editing">
          <button class="btn" @click="addOpen = true"><Plus :size="15" /> Card</button>
          <button class="btn ghost" @click="cancelEdit">Cancel</button>
          <button class="btn primary" :disabled="busy" @click="saveEdit">{{ busy ? 'Saving…' : 'Done' }}</button>
        </template>
        <button v-else class="btn hide-m" @click="startEdit"><Pencil :size="14" /> Edit</button>
      </template>
    </div>
    <nav v-if="list.length" class="tabs" aria-label="Dashboards">
      <RouterLink v-for="x in list" :key="x.uid" :to="{ query: x.uid === list[0].uid ? {} : { d: x.uid } }"
                  :class="{ 'router-link-active': current?.uid === x.uid }" :aria-current="current?.uid === x.uid ? 'page' : undefined"
                  active-class="" exact-active-class="">{{ x.name }}<span v-if="!x.shared" class="faint small"> · yours</span></RouterLink>
      <a v-if="listQ.data.value?.can_own" href="#" class="newtab" title="A new dashboard" @click.prevent="openNew"><Plus :size="14" /> Dashboard</a>
    </nav>
    <div v-if="editing" class="editbar small">
      <LayoutGrid :size="15" /> Drag a card by its handle, resize it from the corner (or focus the handle: arrows move, Shift+arrows resize).
      <span class="grow"></span>
      <a href="#" @click.prevent="renameDash">Rename</a>
      <a href="#" class="c-red" @click.prevent="deleteDash">Delete this dashboard</a>
    </div>

    <div v-if="d && !d.health.jobs.ok" class="warnbox" style="margin-bottom: 16px">
      Jobs are left to <code>labdex worker</code>, and none was seen in the last minute: {{ d.health.jobs.waiting }} waiting.
    </div>

    <div v-if="dash" ref="gridEl" class="dgrid" :class="{ editing }" :style="{ gridTemplateRows: `repeat(${rows}, ${ROW}px)` }">
      <div v-for="it in [...items].sort((a, b) => a.y - b.y || a.x - b.x)" :key="it.id" class="ditem" :style="place(it)" :data-type="it.type">
        <DashItem :item="it" :d="d" />
        <div v-if="editing" class="tools">
          <button class="tbtn grip" :aria-label="`Move ${types[it.type] || it.type}`" title="Drag to move" @pointerdown="pointerDown($event, it, 'move')" @keydown="keyMove($event, it)"><GripVertical :size="15" /></button>
          <span class="tname small">{{ it.config?.title || types[it.type] }}</span>
          <button v-if="['links', 'services', 'note', 'widget'].includes(it.type)" class="tbtn" aria-label="Card settings" title="Settings" @click="openCfg(it)"><Settings2 :size="15" /></button>
          <button class="tbtn" aria-label="Remove card" title="Remove" @click="removeCard(it)"><X :size="15" /></button>
        </div>
        <span v-if="editing" class="resize" title="Drag to resize" aria-hidden="true" @pointerdown="pointerDown($event, it, 'resize')"></span>
      </div>
      <div v-if="!items.length" class="emptydash">
        This dashboard is empty. <a v-if="dash.can_edit && !editing" href="#" @click.prevent="startEdit(); addOpen = true">Add a card</a>
      </div>
    </div>
    <p v-else-if="listQ.loading.value || dashQ.loading.value" class="faint">Loading…</p>

    <!-- add a card -->
    <Modal v-if="addOpen" title="Add a card" @close="addOpen = false">
      <div class="choices">
        <template v-for="(label, t) in types" :key="t">
          <button v-if="t !== 'widget'" class="choice" @click="addCard(t)"><b>{{ label }}</b></button>
        </template>
      </div>
      <template v-if="plugins.length">
        <h3 style="margin: 16px 0 8px">A plugin widget</h3>
        <div class="choices">
          <button v-for="p in plugins" :key="`${p.integration}/${p.widget}`" class="choice" @click="addCard('widget', { integration: p.integration, widget: p.widget })">
            <b>{{ p.label }}</b><span class="mono small faint">{{ p.integration }}</span></button>
        </div>
      </template>
    </Modal>

    <!-- a card's settings -->
    <Modal v-if="cfgDlg" :title="`${types[cfgDlg.it.type]}`" wide @close="cfgDlg = null">
      <Field label="Title"><input v-model="cfgDlg.cfg.title" placeholder="(the card's own)"></Field>
      <template v-if="cfgDlg.it.type === 'links'">
        <div v-for="(l, n) in cfgDlg.cfg.links" :key="n" class="linkrow">
          <input v-model.trim="l.name" placeholder="Name" aria-label="Name">
          <input v-model.trim="l.url" class="mono" placeholder="https://jellyfin.lab.example.net or /hosts" aria-label="Address">
          <input v-model="l.description" placeholder="What it is (optional)" aria-label="Description">
          <button class="btn icon" aria-label="Remove link" @click="cfgDlg.cfg.links.splice(n, 1)"><Trash2 :size="15" /></button>
        </div>
        <div class="row" style="gap: 10px"><button class="btn small" @click="cfgDlg.cfg.links.push({ name: '', url: '', description: '' })"><Plus :size="14" /> Link</button>
          <label class="small">Columns <select v-model.number="cfgDlg.cfg.columns" class="auto"><option v-for="n in [1, 2, 3, 4, 6]" :key="n" :value="n">{{ n }}</option></select></label></div>
        <p v-if="badLink" class="err" role="alert">“{{ badLink.name || badLink.url }}”: an address starts with http:// or https://, or is a page of labdex (/hosts).</p>
        <p v-else class="hint">Addresses start with http:// or https://, or are a page of labdex (/hosts).</p>
      </template>
      <div v-else-if="cfgDlg.it.type === 'services'" class="g3">
        <Field label="Tag" hint="Only services of hosts with this tag"><input v-model.trim="cfgDlg.cfg.tag" list="dash-tags" placeholder="any"></Field>
        <Field label="Zone or area" hint="DC_TC, or DC for the whole area"><select v-model="cfgDlg.cfg.zone"><option value="">any</option>
          <option v-for="z in state.meta.zones" :key="z.code" :value="z.code">{{ z.code }}</option></select></Field>
        <Field label="Category" hint="srv, net, cli …"><input v-model.trim="cfgDlg.cfg.category" placeholder="any"></Field>
        <datalist id="dash-tags"><option v-for="t in tags" :key="t" :value="t" /></datalist>
      </div>
      <Field v-else-if="cfgDlg.it.type === 'note'" label="Text"><textarea v-model="cfgDlg.cfg.text" rows="6" placeholder="Patch day is Tuesday. The spare switch is in the garage."></textarea></Field>
      <Field v-else-if="cfgDlg.it.type === 'widget'" label="Widget">
        <select :value="`${cfgDlg.cfg.integration}/${cfgDlg.cfg.widget}`" @change="[cfgDlg.cfg.integration, cfgDlg.cfg.widget] = $event.target.value.split('/')">
          <option v-for="p in plugins" :key="`${p.integration}/${p.widget}`" :value="`${p.integration}/${p.widget}`">{{ p.label }} ({{ p.integration }})</option></select></Field>
      <template #foot>
        <button class="btn" @click="cfgDlg = null">Cancel</button>
        <button class="btn primary" :disabled="!!badLink" @click="saveCfg">Apply</button>
      </template>
    </Modal>

    <!-- new dashboard / rename -->
    <Modal v-if="newDlg" :title="newDlg.rename ? 'Rename the dashboard' : 'A new dashboard'" :busy="busy" @close="newDlg = null">
      <Field label="Name"><input v-model.trim="newDlg.name" placeholder="Links" @keydown.enter="newDlg.rename ? doRename() : createDash()"></Field>
      <template v-if="!newDlg.rename">
        <Field label="Start with">
          <select v-model="newDlg.start"><option value="links">service links, your links and a note</option><option value="copy">a copy of this one</option><option value="empty">nothing</option></select></Field>
        <label v-if="canShare" class="check"><input v-model="newDlg.shared" type="checkbox"> Shared: everyone sees it, administrators change it</label>
        <p v-else class="hint">It’s yours: only you see it.</p>
      </template>
      <template #foot>
        <button class="btn" @click="newDlg = null">Cancel</button>
        <button class="btn primary" :disabled="!newDlg.name || busy" @click="newDlg.rename ? doRename() : createDash()">{{ newDlg.rename ? 'Rename' : 'Create' }}</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.page.dashpage { max-width: 1500px; }
.dgrid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; position: relative; }
.dgrid.editing { background-image: linear-gradient(to right, color-mix(in srgb, var(--line) 60%, transparent) 1px, transparent 1px);
  background-size: calc((100% + 14px) / 12) 100%; border-radius: var(--r-box); }
.ditem { position: relative; min-width: 0; min-height: 0; container-type: inline-size; }
.ditem > :deep(*:first-child) { height: 100%; overflow: auto; }
.dgrid.editing .ditem { outline: 1px dashed var(--accent); outline-offset: 3px; border-radius: var(--r-box); }
.tools { position: absolute; top: 6px; right: 6px; left: 6px; display: flex; align-items: center; gap: 4px; z-index: 5;
  background: var(--card); border: 1px solid var(--line-strong); border-radius: var(--r-ctl); padding: 2px 4px; box-shadow: 0 4px 14px var(--shadow); }
.tname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.tbtn { background: none; border: 0; color: var(--muted); cursor: pointer; padding: 4px; border-radius: 5px; display: grid; place-items: center; }
.tbtn:hover { color: var(--ink); background: var(--raise); }
.grip { cursor: grab; touch-action: none; }
.grip:active { cursor: grabbing; }
.resize { position: absolute; right: -4px; bottom: -4px; width: 16px; height: 16px; cursor: nwse-resize; z-index: 6; touch-action: none;
  border-right: 3px solid var(--accent); border-bottom: 3px solid var(--accent); border-radius: 0 0 6px 0; }
.editbar { display: flex; align-items: center; gap: 12px; padding: 8px 12px; margin-bottom: 14px; border: 1px solid var(--accent); border-radius: var(--r-ctl);
  background: var(--accent-faint); flex-wrap: wrap; }
.emptydash { grid-column: 1 / -1; color: var(--faint); padding: 30px; text-align: center; border: 1px dashed var(--line-strong); border-radius: var(--r-box); }
.newtab { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); }
.choices { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.choice { text-align: left; background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-ctl); padding: 10px 12px; cursor: pointer; font: inherit;
  color: var(--ink); display: flex; flex-direction: column; gap: 2px; }
.choice:hover { border-color: var(--accent); }
.linkrow { display: grid; grid-template-columns: 1fr 2fr 1.5fr auto; gap: 8px; margin-bottom: 8px; }
@media (max-width: 800px) {
  .dgrid { display: flex; flex-direction: column; }
  .ditem { min-height: 0; }
  .ditem > :deep(*:first-child) { height: auto; }
  .ditem > :deep(section.cardfill) { max-height: 480px; }
  .linkrow { grid-template-columns: 1fr; }
  .choices { grid-template-columns: minmax(0, 1fr); }
}
</style>
