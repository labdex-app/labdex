<script setup>
// Racks as elevations: the front and the rear side by side, unit 1 at the bottom. Click a free
// unit to put a host or a panel there (patch panel, cable management, blank …); a host's ⋯ moves
// it or takes it out, a panel opens its settings. Full-depth things show on both sides.
import { MoreHorizontal, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { confirm } from '../confirm'
import Field from '../components/Field.vue'
import FilterSelect from '../components/FilterSelect.vue'
import HostIcon from '../components/HostIcon.vue'
import HostPicker from '../components/HostPicker.vue'
import Modal from '../components/Modal.vue'
import { useQuery } from '../query'
import { toast, zoneHex } from '../store'

const route = useRoute()
const router = useRouter()
const q = useQuery('/api/racks', { onError: (e) => toast(e.message, 'error') })
const all = computed(() => q.data.value?.racks || [])
const canEdit = computed(() => !!q.data.value?.can_edit)
const site = ref(route.query.site || null)
const sites = computed(() => [...new Set(all.value.map((r) => r.site).filter(Boolean))].sort())
const shown = computed(() => all.value.filter((r) => (!site.value || r.site === site.value) && (!route.query.rack || r.uid === route.query.rack)))
const bySite = computed(() => {
  const g = new Map()
  for (const r of shown.value) { const k = r.site || ''; if (!g.has(k)) g.set(k, []); g.get(k).push(r) }
  return [...g.entries()]
})

const ROW = 22 // px per unit
const SIDES = [['front', 'Front'], ['rear', 'Rear']]
const onSide = (r, side) => r.hosts.filter((h) => h.unit && (h.face === side || h.face === 'full'))
const itemsOn = (r, side) => (r.items || []).filter((i) => i.face === side || i.face === 'full')
const KINDS = { patch: 'patch panel', cables: 'cable management', blank: 'blank panel', shelf: 'shelf', pdu: 'PDU', other: 'other' }
const unplaced = (r) => r.hosts.filter((h) => !h.unit)
const units = (r) => Array.from({ length: r.units }, (_, i) => r.units - i)
const taken = (r, side, u) => [...onSide(r, side), ...itemsOn(r, side)].some((h) => u >= h.unit && u < h.unit + h.height)
const block = (r, h) => ({ gridRow: `${r.units - (h.unit + h.height - 1) + 1} / span ${h.height}`, '--zc': zoneHex(h.zone) })

// ------------------------------------------------------------ placing hosts
const dlg = ref(null) // { rack, host, unit, height, face, moving }
const busy = ref(false)
function placeAt(r, side, u) {
  if (!canEdit.value) return
  dlg.value = { rack: r, what: 'host', host: '', unit: u, height: 1, face: side, moving: false, errors: {}, kind: 'patch', label: '', description: '' }
}
async function pickHost(name) { // its own size comes along
  dlg.value.host = name
  try { dlg.value.height = (await api.get(`/api/hosts/${encodeURIComponent(name)}`)).rack_height || 1 } catch { /* keep 1 */ }
}
function editItem(r, i) { dlg.value = { rack: r, what: 'item', item: i.id, kind: i.kind, label: i.label, description: i.description, unit: i.unit, height: i.height, face: i.face, moving: true, errors: {} } }
async function saveItem() {
  const d = dlg.value
  busy.value = true
  try {
    const body = { kind: d.kind, label: d.label, description: d.description, unit: d.unit, height: d.height, face: d.face }
    if (d.item) await api.put(`/api/racks/${d.rack.uid}/items/${d.item}`, body)
    else await api.post(`/api/racks/${d.rack.uid}/items`, body)
    dlg.value = null
    q.refresh()
  } catch (e) {
    d.errors = e.fields || {}
    if (!e.errors?.length) toast(e.message, 'error')
  } finally { busy.value = false }
}
async function removeItem() {
  const d = dlg.value
  busy.value = true
  try { await api.del(`/api/racks/${d.rack.uid}/items/${d.item}`); dlg.value = null; q.refresh() } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
function edit(r, h) { dlg.value = { rack: r, host: h.name, unit: h.unit, height: h.height, face: h.face, moving: true, errors: {} } }
async function savePlace() {
  const d = dlg.value
  busy.value = true
  try {
    await api.put(`/api/racks/${d.rack.uid}/hosts/${encodeURIComponent(d.host)}`, { unit: d.unit || null, height: d.height, face: d.face })
    dlg.value = null
    q.refresh()
  } catch (e) {
    d.errors = e.fields || {}
    if (!e.errors?.length) toast(e.message, 'error')
  } finally { busy.value = false }
}
async function takeOut() {
  const d = dlg.value
  busy.value = true
  try {
    await api.del(`/api/racks/${d.rack.uid}/hosts/${encodeURIComponent(d.host)}`)
    dlg.value = null
    q.refresh()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
const placeErr = computed(() => { const e = dlg.value?.errors || {}; return e.rack_unit || e.rack_height || e.rack_face || e.rack || e.unit || e.kind || e.face || '' })

// ------------------------------------------------------------ racks themselves
const rackDlg = ref(null)
function newRack() { rackDlg.value = { name: '', site: site.value || '', units: 42, description: '', uid: null, errors: {} } }
function editRack(r) { rackDlg.value = { ...r, errors: {} } }
async function saveRack() {
  const d = rackDlg.value
  busy.value = true
  try {
    const body = { name: d.name, site: d.site, units: d.units, description: d.description }
    if (d.uid) await api.put(`/api/racks/${d.uid}`, body)
    else await api.post('/api/racks', body)
    rackDlg.value = null
    q.refresh()
  } catch (e) {
    d.errors = e.fields || {}
    if (!e.errors?.length) toast(e.message, 'error')
  } finally { busy.value = false }
}
async function removeRack(r) {
  if (!(await confirm({ title: `Delete the rack ${r.name}?`, body: r.hosts.length ? `${r.hosts.length} host(s) in it stay in the inventory, out of any rack.` : 'It’s empty.', action: 'Delete', danger: true }))) return
  try { await api.del(`/api/racks/${r.uid}`); q.refresh() } catch (e) { toast(e.message, 'error') }
}
const setSite = (v) => { site.value = v; router.replace({ query: v ? { site: v } : {} }) }
</script>

<template>
  <div class="page wide">
    <div class="page-head">
      <h1>Racks</h1>
      <span class="sub">{{ all.length }} rack{{ all.length === 1 ? '' : 's' }}</span>
      <span class="grow"></span>
      <button v-if="canEdit" class="btn primary" @click="newRack"><Plus :size="15" /> Rack</button>
    </div>
    <div v-if="sites.length > 1 || route.query.rack" class="filters">
      <FilterSelect :model-value="site" label="Site" :options="sites" @update:model-value="setSite" />
      <RouterLink v-if="route.query.rack" to="/racks" class="btn small ghost">All racks</RouterLink>
    </div>

    <p v-if="q.data.value && !all.length" class="empty">No racks yet. {{ canEdit ? 'Add one, then click a unit to put a host in it.' : '' }}</p>

    <section v-for="[s, list] in bySite" :key="s" class="site">
      <h2 v-if="s || bySite.length > 1" class="sitename">{{ s || 'No site' }}</h2>
      <div class="racks">
        <article v-for="r in list" :key="r.uid" class="card rack">
          <header class="rh">
            <div><b>{{ r.name }}</b> <span class="small faint">{{ r.used }} of {{ r.units }} U used</span>
              <div v-if="r.description" class="small muted">{{ r.description }}</div></div>
            <span class="grow"></span>
            <template v-if="canEdit">
              <button class="btn icon" :aria-label="`Edit rack ${r.name}`" title="Edit rack" @click="editRack(r)"><Pencil :size="15" /></button>
              <button class="btn icon" :aria-label="`Delete rack ${r.name}`" title="Delete rack" @click="removeRack(r)"><Trash2 :size="15" /></button>
            </template>
          </header>
          <div class="rsides">
            <div v-for="[side, label] in SIDES" :key="side" class="rside">
              <div class="sl small faint">{{ label }}</div>
              <div class="elev" :style="{ gridTemplateRows: `repeat(${r.units}, ${ROW}px)` }" role="group" :aria-label="`${r.name} ${label.toLowerCase()}`">
                <template v-for="u in units(r)" :key="u">
                  <span class="un mono" :style="{ gridRow: r.units - u + 1 }" aria-hidden="true">{{ u }}</span>
                  <button v-if="!taken(r, side, u)" class="free" :style="{ gridRow: r.units - u + 1 }" :disabled="!canEdit"
                          :aria-label="`${label} U${u}: free${canEdit ? ', put a host here' : ''}`" :title="canEdit ? `Put a host at U${u}` : `U${u} free`"
                          @click="placeAt(r, side, u)"></button>
                </template>
                <button v-for="i in itemsOn(r, side)" :key="`i${i.id}`" class="item" :class="[`k-${i.kind}`, { full: i.face === 'full' }]"
                        :style="block(r, { ...i, zone: '' })" :disabled="!canEdit" :title="[i.label || KINDS[i.kind], i.description].filter(Boolean).join(': ')"
                        @click="editItem(r, i)">
                  <span class="il ell">{{ i.label || KINDS[i.kind] }}</span><span v-if="i.label || i.description" class="dsub ell">{{ i.label ? KINDS[i.kind] : '' }}{{ i.description ? `${i.label ? ' · ' : ''}${i.description}` : '' }}</span>
                </button>
                <div v-for="h in onSide(r, side)" :key="h.name" class="dev" :class="[`s-${h.status}`, { full: h.face === 'full', tall: h.height > 1 }]" :style="block(r, h)">
                  <RouterLink :to="`/hosts/${h.uid}`" class="dl" :title="`${h.name} · U${h.unit}${h.height > 1 ? `–${h.unit + h.height - 1}` : ''}${h.face === 'full' ? ' · full depth' : ''}${h.hardware ? ` · ${h.hardware}` : ''}`">
                    <HostIcon :host="h" :size="14" /><span class="dn ell">{{ h.fun || h.name }}</span><span v-if="h.height > 1 || h.fun" class="dsub ell mono">{{ h.fun ? h.name : '' }}</span>
                  </RouterLink>
                  <button v-if="canEdit" class="dm" :aria-label="`Move ${h.name}`" title="Move or take out" @click="edit(r, h)"><MoreHorizontal :size="14" /></button>
                </div>
              </div>
            </div>
          </div>
          <div v-if="unplaced(r).length" class="small unpl">In this rack, no unit yet:
            <template v-for="(h, n) in unplaced(r)" :key="h.name"><a href="#" class="mono" @click.prevent="edit(r, h)">{{ h.name }}</a>{{ n < unplaced(r).length - 1 ? ', ' : '' }}</template>
          </div>
        </article>
      </div>
    </section>

    <Modal v-if="dlg" :title="dlg.moving ? (dlg.what === 'item' ? `${dlg.label || KINDS[dlg.kind]}` : `Move ${dlg.host}`) : `${dlg.rack.name}, U${dlg.unit}`" :busy="busy" @close="dlg = null">
      <div v-if="!dlg.moving" class="seg" role="radiogroup" aria-label="What goes here">
        <button type="button" role="radio" :aria-checked="dlg.what === 'host'" :class="{ on: dlg.what === 'host' }" @click="dlg.what = 'host'">A host</button>
        <button type="button" role="radio" :aria-checked="dlg.what === 'item'" :class="{ on: dlg.what === 'item' }" @click="dlg.what = 'item'; dlg.height = 1">A panel or something else</button>
      </div>
      <Field v-if="!dlg.moving && dlg.what === 'host'" label="Host" hint="Its size comes from the host (Overview → Size)"><div class="row" style="gap: 8px; flex-wrap: nowrap"><span v-if="dlg.host" class="mono">{{ dlg.host }}</span>
        <HostPicker style="flex: 1" placeholder="Switch, server, UPS…" @pick="pickHost" /></div></Field>
      <template v-if="dlg.what === 'item'">
        <div class="g3">
          <Field label="What"><select v-model="dlg.kind"><option v-for="(l, k) in KINDS" :key="k" :value="k">{{ l }}</option></select></Field>
          <Field label="Label"><input v-model.trim="dlg.label" placeholder="PP-A"></Field>
        </div>
        <Field label="Description"><input v-model="dlg.description" placeholder="24 × Cat6 to the office, ports 1–12 patched"></Field>
      </template>
      <div class="g3">
        <Field label="Unit" hint="The lowest one it takes"><input v-model.number="dlg.unit" type="number" min="1" :max="dlg.rack.units"></Field>
        <Field label="Height (U)"><input v-model.number="dlg.height" type="number" min="1" :max="dlg.rack.units"></Field>
        <Field label="Side"><select v-model="dlg.face"><option value="front">front</option><option value="rear">rear</option><option value="full">full depth</option></select></Field>
      </div>
      <p v-if="placeErr" class="err" role="alert">{{ placeErr }}</p>
      <template #foot>
        <button v-if="dlg.moving && dlg.what === 'item'" class="btn danger left" :disabled="busy" @click="removeItem">Remove it</button>
        <button v-else-if="dlg.moving" class="btn danger left" :disabled="busy" @click="takeOut">Take out of the rack</button>
        <button class="btn" @click="dlg = null">Cancel</button>
        <button v-if="dlg.what === 'item'" class="btn primary" :disabled="busy" @click="saveItem">{{ dlg.item ? 'Save' : 'Put it here' }}</button>
        <button v-else class="btn primary" :disabled="!dlg.host || busy" @click="savePlace">{{ dlg.moving ? 'Move' : 'Put it here' }}</button>
      </template>
    </Modal>

    <Modal v-if="rackDlg" :title="rackDlg.uid ? `Edit ${rackDlg.name}` : 'New rack'" :busy="busy" @close="rackDlg = null">
      <div class="g3">
        <Field label="Name" :error="rackDlg.errors.name"><input v-model.trim="rackDlg.name" placeholder="R1"></Field>
        <Field label="Site" hint="Room or building; groups racks"><input v-model.trim="rackDlg.site" list="rack-sites" placeholder="Basement"></Field>
        <Field label="Units" :error="rackDlg.errors.units"><input v-model.number="rackDlg.units" type="number" min="1" max="60"></Field>
      </div>
      <datalist id="rack-sites"><option v-for="s in sites" :key="s" :value="s" /></datalist>
      <Field label="Notes"><input v-model="rackDlg.description" placeholder="Patch panel at the top, PDU on the rear"></Field>
      <template #foot>
        <button class="btn" @click="rackDlg = null">Cancel</button>
        <button class="btn primary" :disabled="!rackDlg.name || busy" @click="saveRack">{{ rackDlg.uid ? 'Save' : 'Create rack' }}</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.page.wide { max-width: none; }
.site + .site { margin-top: 26px; }
.sitename { font-size: calc(15px * var(--text)); margin: 0 0 10px; }
.racks { display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }
.rack { margin: 0; padding: 14px 16px; width: 520px; max-width: 100%; }
.rh { display: flex; align-items: flex-start; gap: 6px; margin-bottom: 10px; }
.rsides { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.sl { margin: 0 0 4px 30px; }
.elev { display: grid; grid-template-columns: 26px minmax(0, 1fr); column-gap: 4px; row-gap: 1px; background: var(--panel); border: 1px solid var(--line-strong);
  border-radius: var(--r-ctl); padding: 3px 4px; }
.un { grid-column: 1; font-size: calc(10px * var(--text)); color: var(--faint); text-align: right; line-height: 21px; padding-right: 2px; }
.free { grid-column: 2; border: 1px dashed transparent; background: none; border-radius: var(--r-sm); cursor: pointer; padding: 0; }
.free:not(:disabled):hover, .free:focus-visible { border-color: var(--accent); background: var(--accent-faint); }
.free:disabled { cursor: default; }
.dev { grid-column: 2; display: flex; align-items: stretch; min-width: 0; border-radius: var(--r-sm); background: var(--card);
  border: 1px solid var(--line-strong); border-left: 4px solid var(--zc, var(--line-strong)); position: relative; }
.dev.full { background-image: repeating-linear-gradient(135deg, transparent 0 6px, color-mix(in srgb, var(--ink) 6%, transparent) 6px 8px); }
.dev.s-offline, .dev.s-planned { border-style: dashed; opacity: .8; }
.dl { flex: 1; display: flex; align-items: center; gap: 6px; padding: 0 6px; min-width: 0; color: var(--ink); font-size: calc(12px * var(--text)); }
.dev.tall .dl { flex-wrap: wrap; align-content: center; }
.dl:hover { text-decoration: none; }
.dl:hover .dn { color: var(--accent-soft); }
.dn { font-weight: 500; }
.dsub { font-size: calc(10.5px * var(--text)); color: var(--faint); }
.dev:not(.tall) .dsub { display: none; }
.dm { background: none; border: 0; color: var(--faint); cursor: pointer; padding: 0 4px; border-radius: var(--r-sm); }
.dm:hover { color: var(--ink); background: var(--raise); }
.item { grid-column: 2; display: flex; align-items: center; gap: 6px; min-width: 0; padding: 0 6px; font: inherit; font-size: calc(11.5px * var(--text)); text-align: left;
  border: 1px solid var(--line-strong); border-radius: var(--r-sm); background: var(--raise); color: var(--muted); cursor: pointer; }
.item:disabled { cursor: default; }
.item:not(:disabled):hover { border-color: var(--accent); color: var(--ink); }
.item.k-patch { background-image: radial-gradient(circle, var(--line-strong) 1.5px, transparent 1.6px); background-size: 9px 100%; background-position: 0 center; }
.item .il { flex: none; max-width: 60%; font-weight: 500; }
.item.k-patch span { background: var(--raise); padding: 0 3px; border-radius: var(--r-sm); }
.item.k-cables { border-style: dashed; }
.item.k-blank { background: var(--panel); color: var(--faint); }
.item.full { outline: 1px dotted var(--line-strong); outline-offset: -3px; }
.item .dsub { font-size: calc(10.5px * var(--text)); color: var(--faint); }
.seg { margin-bottom: 4px; }
.unpl { margin-top: 10px; color: var(--muted); }
@media (max-width: 640px) { .rack { width: 100%; } .rsides { grid-template-columns: minmax(0, 1fr); } }
</style>
