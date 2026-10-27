<script setup>
// Approval policies (who approves which plans) and change windows (when approved plans apply).
import { confirm } from '../confirm'
import Modal from './Modal.vue'
import { ArrowDown, ArrowUp, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '../api'
import Field from './Field.vue'
import { toast } from '../store'

const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
const TRIGGERS = [['manual', 'started by someone'], ['hook', 'after inventory changes'], ['schedule', 'on a schedule']]
const policies = ref([])
const windows = ref([])
const people = ref([])
const integrations = ref([])
const editP = ref(null)
const editW = ref(null)
const errors = ref({})
const busy = ref(false)
const browserTz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' } catch { return 'UTC' } })()

async function load() {
  const [p, w, u, g, i] = await Promise.all([api.get('/api/approval-policies'), api.get('/api/change-windows'),
    api.get('/api/admin/users'), api.get('/api/admin/groups'), api.get('/api/integrations')])
  policies.value = p.policies
  windows.value = w
  people.value = [...u.map((x) => x.username), ...g.map((x) => '@' + x.name)]
  integrations.value = i.map((x) => x.name)
}
onMounted(() => load().catch((e) => toast(e.message, 'error')))

const list = (s) => String(s || '').split(/[\s,]+/).filter(Boolean)
function startP(p) {
  errors.value = {}
  editP.value = p
    ? { ...p, integrations: p.integrations.join(', '), zones: p.zones.join(', '), approvers: [...p.approvers], triggers: [...p.triggers] }
    : { name: '', enabled: true, integrations: '*', zones: '*', triggers: ['manual', 'hook', 'schedule'], approvals: 1, approvers: [],
        self_approval: true, delay_minutes: 0, window_id: null, description: '' }
}
function startW(w) {
  errors.value = {}
  editW.value = w ? { ...w, days: [...w.days] } : { name: '', days: ['tue', 'thu'], start: '20:00', end: '22:00', timezone: browserTz, description: '' }
}
async function save(kind) {
  busy.value = true
  errors.value = {}
  try {
    if (kind === 'p') {
      const d = { ...editP.value, integrations: list(editP.value.integrations), zones: list(editP.value.zones),
                  approvals: Number(editP.value.approvals), delay_minutes: Number(editP.value.delay_minutes) || 0 }
      if (d.id) await api.put(`/api/approval-policies/${d.id}`, d)
      else await api.post('/api/approval-policies', d)
      editP.value = null
    } else {
      const d = editW.value
      if (d.id) await api.put(`/api/change-windows/${d.id}`, d)
      else await api.post('/api/change-windows', d)
      editW.value = null
    }
    toast('Saved')
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = Object.fromEntries(e.errors.map((x) => [x.field, x.message]))
    else toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}
async function remove(kind, x) {
  if (!(await confirm({ title: `Delete ${x.name}?`, action: 'Delete', danger: true }))) return
  try {
    await api.del(kind === 'p' ? `/api/approval-policies/${x.id}` : `/api/change-windows/${x.id}`)
    await load()
  } catch (e) { toast(e.message, 'error') }
}
async function move(i, dir) {
  const ids = policies.value.map((p) => p.id)
  const j = i + dir
  if (j < 0 || j >= ids.length) return
  ;[ids[i], ids[j]] = [ids[j], ids[i]]
  try { policies.value = (await api.put('/api/approval-policies/order', { ids })).policies } catch (e) { toast(e.message, 'error') }
}
function toggle(arr, v) { const i = arr.indexOf(v); i < 0 ? arr.push(v) : arr.splice(i, 1) }
const describe = (p) => [
  p.approvals === 0 ? 'applies by itself' : `${p.approvals} approval${p.approvals > 1 ? 's' : ''}${p.approvers.length ? ` from ${p.approvers.join(', ')}` : ''}`,
  p.approvals && !p.self_approval ? 'not by whoever started it' : '',
  p.delay_minutes ? `${p.delay_minutes} min after planning` : '',
  p.window ? `in “${p.window}”` : '',
].filter(Boolean).join(' · ')
const dayLabel = (w) => (w.days.length === 7 ? 'every day' : w.days.join(' '))
const when = (iso) => new Date(iso).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' })
const tzs = computed(() => { try { return Intl.supportedValuesOf('timeZone') } catch { return [browserTz, 'UTC'] } })
</script>

<template>
  <div class="appr">
    <section>
      <div class="sect-head">
        <div>
          <h2>Approval policies</h2>
          <p class="muted small lead">A plan is checked against the policies from the top; the first that matches its integration, the zones of its hosts and how it was started decides how many approvals it needs and when it applies. Without a match you approve your own plans.</p>
        </div>
        <span class="grow"></span>
        <button type="button" class="btn primary" @click="startP(null)"><Plus :size="15" /> Policy</button>
      </div>
      <div class="card flush"><div class="table-wrap">
          <table>
            <thead><tr><th></th><th>Policy</th><th>Applies to</th><th>Needs</th><th></th></tr></thead>
            <tbody>
              <tr v-for="(p, i) in policies" :key="p.id" :class="{ faint: !p.enabled }">
                <td class="nowrap"><button class="btn icon" :disabled="i === 0" aria-label="Up" title="Move up" @click="move(i, -1)"><ArrowUp :size="14" /></button><button class="btn icon" :disabled="i === policies.length - 1" aria-label="Down" title="Move down" @click="move(i, 1)"><ArrowDown :size="14" /></button></td>
                <td><b>{{ p.name }}</b><span v-if="!p.enabled" class="small"> (off)</span><div class="small faint">{{ p.description }}</div></td>
                <td class="small mono">{{ p.integrations.join(' ') }}<div v-if="p.zones.join() !== '*'">zones {{ p.zones.join(' ') }}</div><div v-if="p.triggers.length < 3" class="faint">{{ p.triggers.join(', ') }}</div></td>
                <td class="small">{{ describe(p) }}</td>
                <td class="right nowrap"><button class="btn icon" title="Edit policy" aria-label="Edit policy" @click="startP(p)"><Pencil :size="15" /></button><button class="btn icon" title="Delete policy" aria-label="Delete policy" @click="remove('p', p)"><Trash2 :size="15" /></button></td>
              </tr>
              <tr><td></td><td><b>default</b><div class="small faint">when no policy matches</div></td><td class="small mono">*</td>
                <td class="small">1 approval, may be your own</td><td></td></tr>
            </tbody>
          </table>
      </div></div>
    </section>

    <section>
      <div class="sect-head">
        <div>
          <h2>Change windows</h2>
          <p class="muted small lead">Weekly times when approved plans may apply. A policy with a window holds its approved plans until the window opens; the scheduler applies them then (planning again first if the inventory changed).</p>
        </div>
        <span class="grow"></span>
        <button type="button" class="btn" @click="startW(null)"><Plus :size="15" /> Window</button>
      </div>
      <div class="card flush"><div class="table-wrap">
          <table>
            <thead><tr><th>Window</th><th>When</th><th>Now</th><th>Used by</th><th></th></tr></thead>
            <tbody>
              <tr v-for="w in windows" :key="w.id">
                <td><b>{{ w.name }}</b><div class="small faint">{{ w.description }}</div></td>
                <td class="small"><span class="mono">{{ w.start }}–{{ w.end }}</span> {{ dayLabel(w) }}<div class="faint">{{ w.timezone }}</div></td>
                <td class="small"><span v-if="w.open_now" class="c-green">open</span><span v-else class="faint">opens {{ when(w.next_open) }}</span></td>
                <td class="small">{{ w.policies.join(', ') || '—' }}</td>
                <td class="right nowrap"><button class="btn icon" title="Edit window" aria-label="Edit window" @click="startW(w)"><Pencil :size="15" /></button><button class="btn icon" title="Delete window" aria-label="Delete window" @click="remove('w', w)"><Trash2 :size="15" /></button></td>
              </tr>
              <tr v-if="!windows.length"><td colspan="5" class="empty">No windows: approved plans apply right away.</td></tr>
            </tbody>
          </table>
      </div></div>
    </section>

    <Modal v-if="editP" wide :busy="busy" :title="editP.id ? `Edit ${editP.name}` : 'New policy'" @close="editP = null">
      <form id="pol-form" @submit.prevent="save('p')">
        <div class="stack">
            <Field label="Name" :error="errors.name"><input v-model.trim="editP.name" type="text" required></Field>
            <Field label="Plugin instances" hint="Names or patterns, e.g. opnsense* or *" :error="errors.integrations">
              <input v-model="editP.integrations" type="text" class="mono" list="integ-names"></Field>
            <datalist id="integ-names"><option v-for="n in integrations" :key="n" :value="n" /></datalist>
            <Field label="Zones" hint="Of the hosts a job runs for. A job for no host in particular only matches *." :error="errors.zones">
              <input v-model="editP.zones" type="text" class="mono"></Field>
            <Field label="Started" :error="errors.triggers">
              <div class="row" style="gap: 12px">
                <label v-for="[t, l] in TRIGGERS" :key="t" class="check tick small"><input type="checkbox" :checked="editP.triggers.includes(t)" @change="toggle(editP.triggers, t)"> {{ l }}</label>
              </div></Field>
            <Field label="Approvals needed" hint="0 = the plan applies without anyone (after the delay, inside the window)." :error="errors.approvals">
              <input v-model.number="editP.approvals" type="number" min="0" max="5" style="width: 90px"></Field>
            <Field v-if="editP.approvals > 0" label="Approvers" hint="Empty: anyone who may apply this integration's plans." :error="errors.approvers">
              <select v-model="editP.approvers" multiple size="5"><option v-for="n in people" :key="n" :value="n">{{ n }}</option></select></Field>
            <label v-if="editP.approvals > 0" class="check"><input v-model="editP.self_approval" type="checkbox"> Whoever started the plan may approve it</label>
            <Field label="Delay (minutes)" hint="Applies no sooner than this after the plan was made: time to look and discard." :error="errors.delay_minutes">
              <input v-model.number="editP.delay_minutes" type="number" min="0" style="width: 110px"></Field>
            <Field label="Change window" :error="errors.window_id">
              <select v-model="editP.window_id"><option :value="null">any time</option><option v-for="w in windows" :key="w.id" :value="w.id">{{ w.name }}</option></select></Field>
            <Field label="Description"><input v-model="editP.description" type="text"></Field>
            <label class="check"><input v-model="editP.enabled" type="checkbox"> Enabled</label>
          </div>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="editP = null">Cancel</button>
        <button form="pol-form" class="btn primary" :disabled="busy">Save policy</button>
      </template>
    </Modal>

    <Modal v-if="editW" :busy="busy" :title="editW.id ? `Edit ${editW.name}` : 'New change window'" @close="editW = null">
      <form id="win-form" @submit.prevent="save('w')">
        <div class="stack">
            <Field label="Name" :error="errors.name"><input v-model.trim="editW.name" type="text" required></Field>
            <Field label="Days" :error="errors.days">
              <div class="row" style="gap: 4px">
                <button v-for="d in DAYS" :key="d" type="button" class="chip" :class="{ on: editW.days.includes(d) }" @click="toggle(editW.days, d)">{{ d }}</button>
              </div></Field>
            <div class="row" style="gap: 10px; align-items: flex-start">
              <Field label="From" :error="errors.start"><input v-model="editW.start" type="time" style="width: 120px"></Field>
              <Field label="Until" :error="errors.end" hint="Earlier than From: into the next day."><input v-model="editW.end" type="time" style="width: 120px"></Field>
            </div>
            <Field label="Time zone" :error="errors.timezone"><input v-model.trim="editW.timezone" type="text" list="tz-names" class="mono"></Field>
            <datalist id="tz-names"><option v-for="z in tzs" :key="z" :value="z" /></datalist>
            <Field label="Description"><input v-model="editW.description" type="text"></Field>
          </div>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="editW = null">Cancel</button>
        <button form="win-form" class="btn primary" :disabled="busy">Save window</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.appr > * + * { margin-top: 28px; }
.lead { margin: 4px 0 0; max-width: 90ch; }
.stack > * + * { margin-top: 14px; }
.right { text-align: right; } .nowrap { white-space: nowrap; }
</style>
