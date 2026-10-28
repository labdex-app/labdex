<script setup>
// Every cable in one list: what's printed on it, both ends, medium, speed, length, colour.
// Pick some (or all shown) and print their labels: one wrap-around label per cable, both ends
// written on it, so whichever end you hold tells you where the other goes.
import { Pencil, Printer, Route, Tag } from 'lucide-vue-next'
import CableTrace from '../components/CableTrace.vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import CableSuggestions from '../components/CableSuggestions.vue'
import Field from '../components/Field.vue'
import Modal from '../components/Modal.vue'
import { CABLE_COLOURS, MEDIA, SPEEDS, speedStep } from '../links'
import { useQuery } from '../query'
import { toast } from '../store'

const route = useRoute()
const tracing = ref(null)
const router = useRouter()
const tab = computed(() => (route.query.tab === 'suggested' ? 'suggested' : 'list'))
const setTab = (t) => router.replace({ query: { ...route.query, tab: t === 'list' ? undefined : t } })
const sq = useQuery('/api/cables/suggestions')
const suggestedCount = computed(() => (sq.data.value ? sq.data.value.suggested.length + sq.data.value.conflicts.length : 0))
const q = useQuery('/api/cables', { onError: (e) => toast(e.message, 'error') })
const all = computed(() => q.data.value?.cables || [])
const canEdit = computed(() => !!q.data.value?.can_edit)
const search = ref('')
const unlabelled = ref(false)
const shown = computed(() => {
  const s = search.value.trim().toLowerCase()
  return all.value.filter((c) => (!unlabelled.value || !c.label)
    && (!s || [c.label, c.a_host, c.a_port, c.b_host, c.b_port, c.medium, c.speed, c.colour, c.notes].join(' ').toLowerCase().includes(s)))
})
// the QR code on a cable's label comes here: ?host=&port= opens its trace
onMounted(() => {
  const { host, port } = route.query
  if (host && port) tracing.value = { a_host: String(host), a_uid: String(host), a_port: String(port), label: '' }
})
const picked = ref(new Set())
const allPicked = computed(() => shown.value.length > 0 && shown.value.every((c) => picked.value.has(c.id)))
function togglePick(id) { const p = new Set(picked.value); if (p.has(id)) p.delete(id); else p.add(id); picked.value = p }
function toggleAll() { picked.value = allPicked.value ? new Set() : new Set(shown.value.map((c) => c.id)) }
// the ticked ones, or every cable shown (also when printing with the browser's own menu)
const toPrint = computed(() => (picked.value.size ? all.value.filter((c) => picked.value.has(c.id)) : shown.value))
function print() { window.print() }

const dlg = ref(null)
function edit(c) { dlg.value = { c, f: { label: c.label, length: c.length, colour: c.colour, medium: c.medium, speed: c.speed, notes: c.notes }, errors: {} } }
async function save() {
  const d = dlg.value
  try {
    await api.put(`/api/topology/cables/${d.c.id}`, d.f)
    toast(`Saved ${d.f.label || 'the cable'}`)
    dlg.value = null
    q.refresh()
  } catch (x) {
    d.errors = Object.fromEntries((x.errors || []).map((e) => [e.field.replace(/^cables\[\d+\]\./, ''), e.message]))
    if (!x.errors?.length) toast(x.message, 'error')
  }
}
const swatch = (c) => CABLE_COLOURS.find((x) => x.name === c)?.hex || (/^#[0-9a-f]{6}$/i.test(c || '') ? c : '')
</script>

<template>
  <div class="page wide screen">
    <div class="page-head">
      <h1>Cables</h1>
      <span class="sub">{{ all.length }} cable{{ all.length === 1 ? '' : 's' }}{{ all.filter((c) => !c.label).length ? `, ${all.filter((c) => !c.label).length} without a label` : '' }}</span>
      <span class="grow"></span>
      <template v-if="tab === 'list'">
      <input v-model="search" type="search" placeholder="Label, host, port, colour …" style="max-width: 260px" aria-label="Search cables">
      <button class="btn" :disabled="!shown.length" @click="print"><Printer :size="15" /> Print {{ picked.size ? picked.size : 'all shown' }} label{{ (picked.size || shown.length) === 1 ? '' : 's' }}</button>
      </template>
    </div>
    <nav class="tabs" aria-label="Cables">
      <a href="#" :class="{ 'router-link-active': tab === 'list' }" :aria-current="tab === 'list' ? 'page' : undefined" @click.prevent="setTab('list')">On record <span class="note">{{ all.length }}</span></a>
      <a href="#" :class="{ 'router-link-active': tab === 'suggested' }" :aria-current="tab === 'suggested' ? 'page' : undefined" @click.prevent="setTab('suggested')">
        Seen by switches <span v-if="suggestedCount" class="pill info">{{ suggestedCount }}</span></a>
    </nav>
    <CableSuggestions v-if="tab === 'suggested'" @changed="q.refresh(); sq.refresh()" />
    <template v-else>
    <div class="filters">
      <label class="check small"><input v-model="unlabelled" type="checkbox"> Only cables without a label</label>
      <span v-if="q.data.value" class="small faint">Next free label: <span class="mono">{{ q.data.value.next_label }}</span></span>
    </div>
    <p v-if="q.data.value && !all.length" class="emptyp">No cables yet. Add them on a host's Network tab or draw them on the topology.</p>
    <div v-else-if="all.length" class="card" style="padding: 0">
      <table>
        <thead><tr>
          <th style="width: 34px"><input type="checkbox" class="tick" :checked="allPicked" aria-label="Pick all shown" @change="toggleAll"></th>
          <th>Label</th><th>From</th><th>To</th><th class="hide-m">Kind</th><th class="hide-m">Length</th><th class="hide-m">Notes</th><th :style="{ width: canEdit ? '76px' : '40px' }"></th>
        </tr></thead>
        <tbody>
          <tr v-for="c in shown" :key="c.id">
            <td><input type="checkbox" class="tick" :checked="picked.has(c.id)" :aria-label="`Pick ${c.label || c.a_host + ' ' + c.a_port}`" @change="togglePick(c.id)"></td>
            <td class="mono"><span v-if="swatch(c.colour)" class="cc" :style="{ background: swatch(c.colour) }" :title="c.colour"></span>{{ c.label || '—' }}</td>
            <td><RouterLink :to="`/hosts/${c.a_uid}?tab=network`">{{ c.a_host }}</RouterLink> <span class="mono small">{{ c.a_port }}</span></td>
            <td><RouterLink :to="`/hosts/${c.b_uid}?tab=network`">{{ c.b_host }}</RouterLink> <span class="mono small">{{ c.b_port }}</span></td>
            <td class="hide-m small"><i v-if="speedStep(c.speed)" class="spd" :style="{ background: speedStep(c.speed).colour }"></i>{{ [c.medium, c.speed].filter(Boolean).join(' · ') }}</td>
            <td class="hide-m small">{{ c.length ? `${c.length} m` : '' }}</td>
            <td class="hide-m small muted ell">{{ c.notes }}</td>
            <td class="nowrap"><a class="btn icon" :aria-label="`Label with QR code for ${c.label || 'cable'}`" title="Label with a QR code (SVG)" target="_blank" rel="noopener"
                :href="`/api/cables/label.svg?host=${encodeURIComponent(c.a_host)}&port=${encodeURIComponent(c.a_port)}`"><Tag :size="15" /></a>
              <button class="btn icon" :aria-label="`Trace ${c.label || 'cable'}`" title="Trace end to end, through patch panels" @click="tracing = c"><Route :size="15" /></button>
              <button v-if="canEdit" class="btn icon" :aria-label="`Edit ${c.label || 'cable'}`" @click="edit(c)"><Pencil :size="15" /></button></td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>
    <p v-if="tab === 'list'" class="hint">Each label wraps around the cable near one end and names both ends, so you print two per cable. Fits a 4-column A4 sheet or a label printer roll.</p>

    <Modal v-if="tracing" :title="`Trace ${tracing.label || `${tracing.a_host} ${tracing.a_port}`}`" @close="tracing = null">
      <CableTrace :host="tracing.a_uid || tracing.a_host" :port="tracing.a_port" />
    </Modal>
    <Modal v-if="dlg" :title="`Cable ${dlg.c.a_host} ${dlg.c.a_port} – ${dlg.c.b_host} ${dlg.c.b_port}`" @close="dlg = null">
      <div class="g2">
        <Field label="Label" :error="dlg.errors.label" hint="What's printed on it">
          <div class="inline"><input v-model.trim="dlg.f.label" class="mono" :placeholder="q.data.value?.next_label">
            <button type="button" class="btn small" @click="dlg.f.label = q.data.value.next_label">Next free</button></div></Field>
        <Field label="Length" :error="dlg.errors.length" hint="Metres"><input v-model.trim="dlg.f.length" inputmode="decimal" placeholder="3"></Field>
        <Field label="Colour" :error="dlg.errors.colour">
          <input v-model.trim="dlg.f.colour" list="cable-colours" placeholder="blue"></Field>
        <Field label="Medium"><select v-model="dlg.f.medium"><option value="">—</option><option v-for="m in MEDIA" :key="m" :value="m">{{ m }}</option></select></Field>
        <Field label="Speed"><input v-model.trim="dlg.f.speed" list="cable-speeds-c" placeholder="1G"></Field>
        <Field label="Notes"><input v-model="dlg.f.notes" placeholder="patch panel 12"></Field>
      </div>
      <datalist id="cable-speeds-c"><option v-for="x in SPEEDS" :key="x" :value="x" /></datalist>
      <datalist id="cable-colours"><option v-for="x in CABLE_COLOURS" :key="x.name" :value="x.name" /></datalist>
      <template #foot>
        <span class="small faint left">Both hosts get a history entry</span>
        <button class="btn" @click="dlg = null">Cancel</button>
        <button class="btn primary" @click="save">Save</button>
      </template>
    </Modal>
  </div>

  <!-- only on paper: two labels per cable, one for each end -->
  <div class="sheet" aria-hidden="true">
    <template v-for="c in toPrint" :key="c.id">
      <div v-for="end in ['a', 'b']" :key="end" class="lbl">
        <b class="mono">{{ c.label || '' }}</b>
        <div class="mono"><span>this end</span> {{ end === 'a' ? c.a_host : c.b_host }} {{ end === 'a' ? c.a_port : c.b_port }}</div>
        <div class="mono"><span>other end</span> {{ end === 'a' ? c.b_host : c.a_host }} {{ end === 'a' ? c.b_port : c.a_port }}</div>
        <div class="meta">{{ [c.medium, c.speed, c.length ? `${c.length} m` : ''].filter(Boolean).join(' · ') }}</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cc { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; border: 1px solid var(--line-strong); vertical-align: -1px; }
.spd { display: inline-block; width: 12px; height: 4px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
.inline { display: flex; gap: 6px; }
.sheet { display: none; }
@media print {
  .screen { display: none; }
  .sheet { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3mm; color: #000; }
  .lbl { border: 0.3mm dashed #999; padding: 2mm 3mm; font-size: 8pt; line-height: 1.3; break-inside: avoid; min-height: 18mm; }
  .lbl b { font-size: 11pt; display: block; }
  .lbl span { color: #555; font-size: 6.5pt; text-transform: none; }
  .lbl .meta { color: #555; font-size: 6.5pt; }
}
</style>
