<script setup>
// Every cable in one list: what's printed on it, both ends, medium, speed, length, colour.
// Pick some (or all shown) and print their labels (the Labels page): two per cable, one for each end
// written on it, so whichever end you hold tells you where the other goes.
import { Pencil, Printer, Route } from 'lucide-vue-next'
import CableTrace from '../components/CableTrace.vue'
import { computed, ref, watch } from 'vue'
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
const picked = ref(new Set())
const allPicked = computed(() => shown.value.length > 0 && shown.value.every((c) => picked.value.has(c.id)))
function togglePick(id) { const p = new Set(picked.value); if (p.has(id)) p.delete(id); else p.add(id); picked.value = p }
function toggleAll() { picked.value = allPicked.value ? new Set() : new Set(shown.value.map((c) => c.id)) }
// the ticked ones, or every cable shown (also when printing with the browser's own menu)
const toPrint = computed(() => (picked.value.size ? all.value.filter((c) => picked.value.has(c.id)) : shown.value))
// on the Labels page: QR codes, label printer and sheet sizes
function print() { router.push({ path: '/labels', query: { what: 'cables', cable: toPrint.value.map((c) => String(c.id)) } }) }
// a scanned cable label: /cables?cable=<id> opens its trace
watch([() => route.query.cable, all], ([id]) => {
  if (id && !tracing.value) tracing.value = all.value.find((c) => String(c.id) === String(id)) || null
}, { immediate: true })

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
  <div class="page wide">
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
            <td class="nowrap"><button class="btn icon" :aria-label="`Trace ${c.label || 'cable'}`" title="Trace end to end, through patch panels" @click="tracing = c"><Route :size="15" /></button>
              <button v-if="canEdit" class="btn icon" :aria-label="`Edit ${c.label || 'cable'}`" @click="edit(c)"><Pencil :size="15" /></button></td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>
    <p v-if="tab === 'list'" class="hint">Each label goes near one end and names both ends, so you print two per cable: on a label printer roll, tape or an A4 sheet, with a QR code that opens the cable's trace.</p>

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
</template>

<style scoped>
.cc { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; border: 1px solid var(--line-strong); vertical-align: -1px; }
.spd { display: inline-block; width: 12px; height: 4px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
.inline { display: flex; gap: 6px; }
</style>
