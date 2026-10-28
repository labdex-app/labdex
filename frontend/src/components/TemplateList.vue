<script setup>
// Job templates of one integration: saved parameters (and host), run by a click or on a schedule.
import { confirm } from '../confirm'
import Modal from './Modal.vue'
import { Pencil, Play, Plus, Trash2 } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError, enc } from '../api'
import Field from './Field.vue'
import SettingsForm from './SettingsForm.vue'
import { ago, stamp, toast } from '../store'

const props = defineProps({ integration: Object })  // the integration detail (with plugin spec)
const router = useRouter()
const list = ref([])
const edit = ref(null)
const errors = ref({})
const busy = ref(false)
const hosts = ref([])
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' } catch { return 'UTC' } })()

const actions = computed(() => (props.integration.plugin?.actions || []).filter((a) => a.mode !== 'export'))
const action = computed(() => actions.value.find((a) => a.id === edit.value?.action))

async function load() {
  list.value = await api.get(`/api/job-templates?integration=${enc(props.integration.name)}`)
}
onMounted(() => load().catch(() => {}))

async function start(t) {
  errors.value = {}
  if (!hosts.value.length) hosts.value = (await api.get('/api/hosts?fields=name').catch(() => [])).map((h) => h.name)
  edit.value = t
    ? { ...t, params: { ...t.params }, days: [...t.days] }
    : { name: '', action: actions.value[0]?.id, host: '', params: {}, description: '', schedule: '', every_minutes: 60, days: ['mon'], at: '03:00', timezone: tz, enabled: true }
  fillParams()
}
function fillParams() {
  for (const f of action.value?.params || []) if (!(f.key in edit.value.params)) edit.value.params[f.key] = f.default ?? (f.type === 'list' ? [] : f.type === 'bool' ? false : '')
}
async function save() {
  busy.value = true
  errors.value = {}
  const d = { ...edit.value, integration: props.integration.name }
  try {
    if (d.id) await api.put(`/api/job-templates/${d.id}`, d)
    else await api.post('/api/job-templates', d)
    edit.value = null
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = Object.fromEntries(e.errors.map((x) => [x.field, x.message]))
    else toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}
async function runIt(t) {
  try {
    const job = await api.post(`/api/job-templates/${t.id}/run`)
    router.push(`/jobs/${job.id}`)
  } catch (e) { toast(e.message, 'error') }
}
async function remove(t) {
  if (!(await confirm({ title: `Delete the template “${t.name}”?`, action: 'Delete', danger: true }))) return
  try { await api.del(`/api/job-templates/${t.id}`); await load() } catch (e) { toast(e.message, 'error') }
}
const paramErrors = computed(() => Object.fromEntries(Object.entries(errors.value).filter(([k]) => k.startsWith('params.')).map(([k, v]) => [k.slice(7), v])))
const when = (t) => (t.schedule === 'every' ? (t.every_minutes % 60 === 0 ? `every ${t.every_minutes / 60} h` : `every ${t.every_minutes} min`)
  : t.schedule === 'weekly' ? `${t.days.length === 7 ? 'daily' : t.days.join(' ')} ${t.at} ${t.timezone}` : '')
const paramText = (t) => Object.entries(t.params || {}).filter(([, v]) => v !== '' && v != null && !(Array.isArray(v) && !v.length))
  .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(',') : v}`).join(' ')
function toggleDay(d) { const a = edit.value.days; const i = a.indexOf(d); i < 0 ? a.push(d) : a.splice(i, 1) }
</script>

<template>
  <div class="card">
    <div class="row" style="margin-bottom: 6px">
      <h2 style="margin: 0">Templates</h2><span class="grow"></span>
      <button v-if="integration.can_apply" type="button" class="btn small" @click="start(null)"><Plus :size="14" /> Template</button>
    </div>
    <ul v-if="list.length" class="plain tlist">
      <li v-for="t in list" :key="t.id">
        <div class="row" style="flex-wrap: nowrap">
          <div style="flex: 1; min-width: 0">
            <b>{{ t.name }}</b> <span class="small muted">{{ t.action_label }}<template v-if="t.host"> · <span class="mono">{{ t.host }}</span></template></span>
            <div v-if="paramText(t)" class="small mono faint ell">{{ paramText(t) }}</div>
            <div class="small faint">
              <template v-if="t.schedule"><span :class="{ 'c-yellow': !t.enabled }">{{ when(t) }}<template v-if="!t.enabled"> (paused)</template></span>
                <template v-if="t.next_run_at"> · next {{ stamp(t.next_run_at) }}</template></template>
              <template v-if="t.last_job_id"> · last <RouterLink :to="`/jobs/${t.last_job_id}`">{{ ago(t.last_run_at) }}</RouterLink></template>
            </div>
          </div>
          <button class="btn small" :disabled="!t.can_run || !integration.enabled" @click="runIt(t)"><Play :size="13" /> Run</button>
          <button v-if="integration.can_apply" class="btn icon" title="Edit template" aria-label="Edit template" @click="start(t)"><Pencil :size="15" /></button>
          <button v-if="integration.can_apply" class="btn icon" title="Delete template" aria-label="Delete template" @click="remove(t)"><Trash2 :size="15" /></button>
        </div>
      </li>
    </ul>
    <p v-else class="small muted" style="margin: 0">An action with its parameters saved: run it in one click, or on a schedule. Scheduled plans go through approvals like any other.</p>

    <Modal v-if="edit" wide :busy="busy" :title="edit.id ? `Edit ${edit.name}` : 'New template'" @close="edit = null">
    <form id="tpl-form" class="tform" @submit.prevent="save">
      <div class="grid2">
        <Field label="Name" :error="errors.name"><input v-model.trim="edit.name" type="text" required></Field>
        <Field label="Action" :error="errors.action">
          <select v-model="edit.action" @change="edit.params = {}; fillParams()"><option v-for="a in actions" :key="a.id" :value="a.id">{{ a.label || a.id }}{{ a.mode === 'plan' ? ' (plan)' : '' }}</option></select></Field>
      </div>
      <Field v-if="action?.scope === 'host'" label="Host" :error="errors.host">
        <input v-model.trim="edit.host" type="text" class="mono" list="tpl-hosts" required></Field>
      <datalist id="tpl-hosts"><option v-for="h in hosts" :key="h" :value="h" /></datalist>
      <SettingsForm v-if="action?.params?.length" :fields="action.params" v-model:values="edit.params" :errors="paramErrors" />
      <Field label="Description"><input v-model="edit.description" type="text"></Field>
      <Field label="Schedule" :error="errors.schedule || errors.every_minutes || errors.at || errors.days || errors.timezone">
        <div class="row" style="gap: 8px">
          <select v-model="edit.schedule" class="auto"><option value="">only by hand</option><option value="every">every …</option><option value="weekly">at a time</option></select>
          <template v-if="edit.schedule === 'every'"><input v-model.number="edit.every_minutes" type="number" min="5" style="width: 90px"> <span class="small muted">minutes</span></template>
          <template v-if="edit.schedule === 'weekly'">
            <button v-for="d in DAYS" :key="d" type="button" class="chip" :class="{ on: edit.days.includes(d) }" @click="toggleDay(d)">{{ d }}</button>
            <input v-model="edit.at" type="time" style="width: 110px">
            <input v-model.trim="edit.timezone" type="text" class="mono" style="width: 160px" aria-label="Time zone">
          </template>
        </div>
      </Field>
      <label v-if="edit.schedule" class="check small"><input v-model="edit.enabled" type="checkbox"> Scheduled runs on</label>
      <p v-if="edit.schedule" class="hint">Runs unattended as “template {{ edit.name || '…' }}”, so a schedule needs admin rights on this integration.</p>
    </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="edit = null">Cancel</button>
        <button form="tpl-form" class="btn primary" :disabled="busy">Save template</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.tlist li { padding: 10px 0; border-top: 1px solid var(--line); }
.tlist li:first-child { border-top: 0; }
.tform { display: flex; flex-direction: column; gap: 14px; }
</style>
