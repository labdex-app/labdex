<script setup>
// Areas and the zones in them. Every area has a default zone (just the area, no trust level);
// further zones are made one by one, each with its own rank and colour.
import { computed, onMounted, reactive, ref } from 'vue'
import { api, ApiError, enc } from '../api'
import { changed, toast } from '../store'
import Field from './Field.vue'
import Modal from './Modal.vue'
import ColourPicker from './ColourPicker.vue'
import { confirm } from '../confirm'
import { Pencil, Trash2, Plus } from 'lucide-vue-next'

const z = ref(null)
const errors = ref({})
// what people type becomes a code (the server does the same): "Trusted clients" → TRUSTED_CLIENTS;
// an area's code can't hold "_", it separates the area from the zone
const cleanCode = (t, area = false) => String(t || '').trim().toUpperCase().replace(/[\s.\-_]+/g, area ? '' : '_')
  .replace(/[^A-Z0-9_]/g, '').replace(/_+/g, '_').replace(/^_|_$/g, '')
const blankArea = () => ({ code: '', name: '', description: '', colour: '', _orig: null })
const blankZone = (area = '') => ({ area, suffix: '', name: '', rank: 3, colour: '', description: '', _orig: null })
const area = reactive(blankArea())
const zone = reactive(blankZone())
const zoneFormFor = ref('')
const areaOpen = ref(false)
const busy = ref(false)

async function load() { z.value = await api.get('/api/zones') }
onMounted(load)

async function call(fn, msg) {
  errors.value = {}
  try {
    z.value = await fn()
    toast(msg)
    await changed()
    return true
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
    return false
  }
}
const saveArea = async () => {
  const { _orig, ...rest } = area
  const body = { ...rest, code: cleanCode(rest.code, true) }
  busy.value = true
  const ok = await call(() => (_orig ? api.put(`/api/zones/areas/${enc(_orig)}`, body) : api.post('/api/zones/areas', body)), `Saved area ${body.code}`)
  busy.value = false
  if (ok) { Object.assign(area, blankArea()); areaOpen.value = false }
}
const saveZone = async () => {
  const { _orig, suffix, ...rest } = zone
  const body = { ...rest, code: suffix ? `${rest.area}_${cleanCode(suffix)}` : rest.area, rank: rest.rank === '' ? null : rest.rank }
  busy.value = true
  const ok = await call(() => (_orig ? api.put(`/api/zones/zones/${enc(_orig)}`, body) : api.post('/api/zones/zones', body)), `Saved zone ${body.code}`)
  busy.value = false
  if (ok) {
    Object.assign(zone, blankZone())
    zoneFormFor.value = ''
  }
}
function editZone(x) {
  Object.assign(zone, { ...blankZone(x.area), ...x, suffix: x.default ? '' : x.code.slice(x.area.length + 1), rank: x.rank ?? '', _orig: x.code })
  errors.value = {}
  zoneFormFor.value = x.area
}
function newZone(a) { errors.value = {}; Object.assign(zone, blankZone(a)); zoneFormFor.value = a }
function editArea(a) { errors.value = {}; Object.assign(area, { ...blankArea(), ...a, _orig: a.code }); areaOpen.value = true }
function newArea() { errors.value = {}; Object.assign(area, blankArea()); areaOpen.value = true }
const zoneArea = computed(() => (z.value?.areas || []).find((a) => a.code === zoneFormFor.value) || { code: zoneFormFor.value, name: '' })
async function del(part, code) {
  const ok = await confirm({ title: `Delete ${part === 'areas' ? 'area' : 'zone'} ${code}?`, body: part === 'areas' ? 'Its zones go with it. Networks and hosts still using them block the delete.' : 'Networks and hosts still in it block the delete.', action: 'Delete', danger: true })
  if (ok) call(() => api.del(`/api/zones/${part}/${enc(code)}`), `Deleted ${code}`)
}
const zonesOf = (a) => z.value.zones.filter((x) => x.area === a)
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`
const used = (x) => x.networks + x.hosts
const areaHex = computed(() => Object.fromEntries((z.value?.areas || []).map((a) => [a.code, a.colour])))
</script>

<template>
  <div v-if="z" class="zones">
    <div class="sect-head">
      <div>
        <p class="muted small lead">
          An <b>area</b> is where something is (datacenter, campus, DMZ); its default zone carries the area's code (<code>DC</code>).
          Add a <b>zone</b> per trust level you need (<code>DC_TC</code>, <code>CA_UT</code> for IoT). A host may not sit in a network ranked above it.
        </p>
      </div>
      <span class="grow"></span>
      <button v-if="!z.areas.length" class="btn" @click="call(() => api.post('/api/zones/example'), 'Loaded the example set')">Load the example set</button>
      <button class="btn primary" @click="newArea"><Plus :size="15" /> Area</button>
    </div>

    <div v-if="!z.areas.length" class="list-empty">No areas yet. Add one, or load the example set to start from a datacenter, a campus and a DMZ.</div>

    <section v-for="a in z.areas" :key="a.code" class="area">
      <div class="ahead">
        <span class="swatch" :style="{ background: a.colour || 'var(--line)' }"></span>
        <div class="ah"><span class="mono">{{ a.code }}</span> {{ a.name }}</div>
        <span v-if="a.code === z.dmz_area" class="tag">DMZ</span>
        <span v-if="a.description" class="small faint adesc">{{ a.description }}</span>
        <span class="grow"></span>
        <button class="btn small" @click="newZone(a.code)"><Plus :size="14" /> Zone</button>
        <button class="btn icon" title="Edit area" aria-label="Edit area" @click="editArea(a)"><Pencil :size="15" /></button>
        <button class="btn icon" title="Delete area" aria-label="Delete area" @click="del('areas', a.code)"><Trash2 :size="15" /></button>
      </div>
      <div class="list">
        <div v-for="x in zonesOf(a.code)" :key="x.code" class="li">
          <div class="li-main">
            <div class="li-title"><RouterLink :to="`/zones/${x.uid}`" class="zone zlink" :style="{ '--zc': x.hex }">{{ x.code }}</RouterLink> {{ x.name }}
              <span v-if="x.default" class="faint small">area default</span></div>
            <div v-if="x.description" class="li-sub">{{ x.description }}</div>
          </div>
          <div class="meta small">
            <span title="Rank: higher is more trusted">rank <b class="mono">{{ x.rank ?? '—' }}</b></span>
            <span :class="{ faint: !used(x) }">{{ plural(x.networks, 'network') }}, {{ plural(x.hosts, 'host') }}</span>
          </div>
          <div class="li-actions">
            <button class="btn icon" title="Edit zone" aria-label="Edit zone" @click="editZone(x)"><Pencil :size="15" /></button>
            <button v-if="!x.default" class="btn icon" title="Delete zone" aria-label="Delete zone" @click="del('zones', x.code)"><Trash2 :size="15" /></button>
            <span v-else class="btn icon ph" aria-hidden="true"></span>
          </div>
        </div>
      </div>
    </section>

    <Modal v-if="zoneFormFor" :title="zone._orig ? `Edit zone ${zone._orig}` : `New zone in ${zoneFormFor}`" :busy="busy" @close="zoneFormFor = ''">
      <form id="zone-form" class="fstack" @submit.prevent="saveZone">
        <div class="grid3">
          <Field label="Code" :error="errors.code" :hint="zone._orig && !zone.suffix ? 'the area default' : ''">
            <div class="row" style="gap: 4px; flex-wrap: nowrap"><span class="mono">{{ zoneArea.code }}<template v-if="!(zone._orig && zone._orig === zoneArea.code)">_</template></span>
              <input v-if="!(zone._orig && zone._orig === zoneArea.code)" v-model="zone.suffix" class="mono" placeholder="Trusted clients" required></div></Field>
        <p v-if="zone.suffix && cleanCode(zone.suffix) !== zone.suffix" class="hint" style="margin-top: -6px">The code: <span class="mono">{{ zoneArea.code }}_{{ cleanCode(zone.suffix) }}</span></p>
          <Field label="Name" :error="errors.name"><input v-model.trim="zone.name" :placeholder="`${zoneArea.name}, trusted`"></Field>
          <Field label="Rank" :error="errors.rank" hint="Higher = more trusted">
            <input v-model.number="zone.rank" type="number" min="0" max="99"></Field>
        </div>
        <Field label="Colour" :error="errors.colour" hint="Empty = the area's colour"><ColourPicker v-model="zone.colour" :none-colour="areaHex[zoneArea.code] || ''" none-title="the area's colour" /></Field>
        <Field label="Description"><input v-model="zone.description"></Field>
        <p v-if="zone._orig && zone.suffix && zone._orig !== `${zoneArea.code}_${cleanCode(zone.suffix)}`" class="hint">Renames the zone on every network, host, alias member, policy cell and role scope.</p>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="zoneFormFor = ''">Cancel</button>
        <button form="zone-form" class="btn primary" :disabled="busy">{{ zone._orig ? 'Save zone' : 'Add zone' }}</button>
      </template>
    </Modal>

    <Modal v-if="areaOpen" :title="area._orig ? `Edit area ${area._orig}` : 'New area'" :busy="busy" @close="areaOpen = false">
      <form id="area-form" class="fstack" @submit.prevent="saveArea">
        <div class="grid2">
          <Field label="Code" :error="errors.code" :hint="area.code && cleanCode(area.code, true) !== area.code ? `The code: ${cleanCode(area.code, true)}` : 'Letters and digits, up to 16; short is handy (DC, HOME)'">
            <input v-model="area.code" class="mono" placeholder="DC" required></Field>
          <Field label="Name" :error="errors.name"><input v-model.trim="area.name" placeholder="Datacenter" required></Field>
        </div>
        <Field label="Colour" :error="errors.colour"><ColourPicker v-model="area.colour" /></Field>
        <Field label="Description"><input v-model="area.description"></Field>
        <p v-if="area._orig" class="hint">Changing the code renames its zones everywhere.</p>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="areaOpen = false">Cancel</button>
        <button form="area-form" class="btn primary" :disabled="busy">{{ area._orig ? 'Save area' : 'Add area' }}</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.zones > * + * { margin-top: 18px; }
.lead { margin: 0; max-width: 78ch; }
.ahead { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.adesc { margin-left: 4px; }
.swatch { width: 12px; height: 12px; border-radius: var(--r-sm); flex: none; }
.ah { font-size: calc(15px * var(--text)); font-weight: 600; }
.ah .mono { margin-right: 6px; }
.ph { visibility: hidden; }
.meta { display: grid; grid-template-columns: 56px 150px; gap: 16px; color: var(--muted); white-space: nowrap; text-align: right; }
.fstack > * + * { margin-top: 14px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 700px) { .grid2, .grid3 { grid-template-columns: minmax(0, 1fr); } .meta { display: none; } }
.zlink:hover { text-decoration: none; filter: brightness(1.15); }
</style>
