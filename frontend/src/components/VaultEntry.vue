<script setup>
// One vault entry in a dialog: its fields (hidden), reveal or copy one at a time, its versions,
// and (for those who may) editing. A browser session confirms its password before the first
// reveal (every 5 minutes); a shown value hides itself again after 30 seconds.
import { Copy as CopyIcon, Eye, EyeOff, KeyRound, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref } from 'vue'
import { api, ApiError } from '../api'
import { confirm } from '../confirm'
import Field from './Field.vue'
import HostPicker from './HostPicker.vue'
import Modal from './Modal.vue'
import { toast } from '../store'

// uid: an existing entry; or null with `start` ({path, host}) to make a new one
const props = defineProps({ uid: { type: String, default: null }, start: { type: Object, default: () => ({}) }, sealed: Boolean, reauth: Boolean })
const emit = defineEmits(['close', 'changed'])
const e = ref(null)
const shown = ref({}) // field -> value, while visible
const timers = {}
const busy = ref(false)
const editing = ref(!props.uid)
const form = ref(null)
const errors = ref({})
const pw = ref(null) // { password, error, then }

async function load() {
  if (!props.uid) { startEdit(); return }
  try { e.value = await api.get(`/api/vault/entries/${props.uid}`) } catch (x) { toast(x.message, 'error'); emit('close') }
}
load()
onBeforeUnmount(() => Object.values(timers).forEach(clearTimeout))

// ------------------------------------------------------------------ reveal
let askFirst = props.reauth // the status said so: ask before trying, not after a refusal
const confirmed = () => new Promise((resolve) => { pw.value = { password: '', error: '', then: resolve } })
async function fetchValue(field, version) {
  if (askFirst) { await confirmed(); askFirst = false }
  try {
    return (await api.post(`/api/vault/entries/${props.uid}/reveal`, { field, version })).value
  } catch (x) {
    if (x instanceof ApiError && x.status === 403 && x.fields.password === 'reauth') { // confirm the password, then again
      return new Promise((resolve) => { pw.value = { password: '', error: '', then: async () => resolve(await fetchValue(field, version)) } })
    }
    toast(x.message, 'error')
    return null
  }
}
async function reveal(field) {
  if (shown.value[field] != null) { hide(field); return }
  const v = await fetchValue(field)
  if (v == null) return
  shown.value = { ...shown.value, [field]: v }
  clearTimeout(timers[field])
  timers[field] = setTimeout(() => hide(field), 30000)
}
const old = ref({}) // 'version:field' -> value of an older version, briefly
async function revealOld(version, field) {
  const k = `${version}:${field}`
  if (old.value[k] != null) { const o = { ...old.value }; delete o[k]; old.value = o; return }
  const v = await fetchValue(field, version)
  if (v == null) return
  old.value = { ...old.value, [k]: v }
  setTimeout(() => { const o = { ...old.value }; delete o[k]; old.value = o }, 30000)
}
function hide(field) { const s = { ...shown.value }; delete s[field]; shown.value = s }
async function copy(field) {
  const v = shown.value[field] ?? (await fetchValue(field))
  if (v == null) return
  try { await navigator.clipboard.writeText(v); toast(`${field} copied`) } catch { toast('The browser doesn’t allow copying here', 'error') }
}
async function sendPassword() {
  try {
    await api.post('/api/auth/confirm', { password: pw.value.password })
    const then = pw.value.then
    pw.value = null
    then()
  } catch (x) { pw.value.error = x.fields?.password || x.message }
}

// ------------------------------------------------------------------ edit
function startEdit() {
  const cur = e.value
  form.value = {
    path: cur?.path || props.start.path || '', host: cur?.host || props.start.host || '', description: cur?.description || '',
    fields: cur ? cur.fields.map((n) => ({ name: n, value: '', keep: true })) : [{ name: 'username', value: '', keep: false }, { name: 'password', value: '', keep: false }],
  }
  errors.value = {}
  editing.value = true
}
function generate(f) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.!'
  const buf = new Uint32Array(24)
  crypto.getRandomValues(buf)
  f.value = Array.from(buf, (n) => chars[n % chars.length]).join('')
  f.keep = false
  f.show = true
}
const valuesChanged = computed(() => form.value && (form.value.fields.some((f) => !f.keep) || (e.value && form.value.fields.length !== e.value.fields.length)
  || (e.value && form.value.fields.some((f, i) => f.name !== e.value.fields[i]))))
async function save() {
  const f = form.value
  const body = { path: f.path, host: f.host, description: f.description }
  if (!e.value || valuesChanged.value) body.fields = Object.fromEntries(f.fields.filter((x) => x.name).map((x) => [x.name, x.keep ? null : x.value]))
  busy.value = true
  errors.value = {}
  try {
    const saved = e.value ? await api.put(`/api/vault/entries/${e.value.uid}`, body) : await api.post('/api/vault/entries', body)
    toast(`Saved ${saved.path}`)
    emit('changed')
    if (!e.value) { emit('close'); return }
    editing.value = false
    shown.value = {}
    await load()
  } catch (x) {
    errors.value = x.fields || {}
    if (!x.errors?.length) toast(x.message, 'error')
  } finally { busy.value = false }
}
async function remove() {
  if (!(await confirm({ title: `Delete ${e.value.path}?`, body: 'Every version of it goes; nobody can reveal it any more.', action: 'Delete', danger: true }))) return
  await api.del(`/api/vault/entries/${e.value.uid}`)
  toast(`Deleted ${e.value.path}`)
  emit('changed')
  emit('close')
}
const stamp = (s) => (s ? new Date(s).toLocaleString() : '')
</script>

<template>
  <Modal :title="editing ? (e ? `Edit ${e.path}` : 'New secret') : e?.path || '…'" wide :busy="busy" @close="emit('close')">
    <template v-if="editing && form">
      <p v-if="sealed && (!e || valuesChanged)" class="warnbox small m-0">The vault is sealed: path, host and description can change, values can’t.</p>
      <div class="g2">
        <Field label="Path" :error="errors.path" hint="hosts/srv-pm-pve-01/root, wifi/iot …"><input v-model.trim="form.path" class="mono" placeholder="hosts/srv-pm-pve-01/root"></Field>
        <Field label="Host" :error="errors.host" hint="Optional: shows on the host, and its rights apply">
          <div class="row" style="gap: 8px; flex-wrap: nowrap"><span v-if="form.host" class="mono">{{ form.host }}</span>
            <HostPicker style="flex: 1" :placeholder="form.host ? 'another host…' : 'none'" @pick="(n) => (form.host = n)" />
            <button v-if="form.host" type="button" class="btn small ghost" @click="form.host = ''">none</button></div></Field>
      </div>
      <Field label="Description"><input v-model="form.description" placeholder="IPMI console; the key is in the drawer"></Field>
      <div class="flabel">Fields</div>
      <div v-for="(f, n) in form.fields" :key="n" class="frow">
        <input v-model.trim="f.name" class="mono" placeholder="password" :aria-label="`Field ${n + 1} name`">
        <input v-if="f.keep" class="mono" disabled value="(unchanged)" :aria-label="`${f.name}: unchanged`">
        <input v-else v-model="f.value" :type="f.show ? 'text' : 'password'" autocomplete="new-password" class="mono" :aria-label="`${f.name} value`" placeholder="value">
        <button type="button" class="btn icon" :title="f.keep ? 'Give it a new value' : f.show ? 'Hide' : 'Show'" :aria-label="f.keep ? `New value for ${f.name}` : 'Show or hide'"
                @click="f.keep ? (f.keep = false) : (f.show = !f.show)"><component :is="f.keep ? Plus : f.show ? EyeOff : Eye" :size="15" /></button>
        <button type="button" class="btn icon" title="Generate a password" aria-label="Generate a password" @click="generate(f)"><RefreshCw :size="15" /></button>
        <button type="button" class="btn icon" title="Remove field" :aria-label="`Remove ${f.name}`" @click="form.fields.splice(n, 1)"><Trash2 :size="15" /></button>
      </div>
      <div v-if="errors.fields" class="err">{{ errors.fields }}</div>
      <button type="button" class="btn small" @click="form.fields.push({ name: '', value: '', keep: false })"><Plus :size="14" /> Field</button>
    </template>

    <template v-else-if="e">
      <div class="row small muted" style="gap: 10px">
        <span v-if="e.host">on <RouterLink :to="`/hosts/${e.host_uid}?tab=secrets`" class="mono">{{ e.host }}</RouterLink></span>
        <span>version {{ e.version }} · {{ e.updated_by }} · {{ stamp(e.updated_at) }}</span>
      </div>
      <p v-if="e.description" class="m-0">{{ e.description }}</p>
      <p v-if="e.used_by?.length" class="small muted m-0">Used by {{ e.used_by.join(', ') }}: they read it when they run.
        Point at a field with <span class="mono">vault:{{ e.path }}#{{ e.fields[0] }}</span>.</p>
      <p v-else-if="e.can_reveal" class="small faint m-0">Plugins and Ansible variables can point at it: <span class="mono">vault:{{ e.path }}#{{ e.fields[0] }}</span>.</p>
      <div class="fields">
        <div v-for="f in e.fields" :key="f" class="fld">
          <span class="mono fname">{{ f }}</span>
          <span class="mono fval" :class="{ hidden: shown[f] == null }">{{ shown[f] ?? '••••••••••••' }}</span>
          <template v-if="e.can_reveal">
            <button class="btn icon" :disabled="sealed" :title="shown[f] != null ? 'Hide' : 'Reveal (written to the history)'" :aria-label="`${shown[f] != null ? 'Hide' : 'Reveal'} ${f}`" @click="reveal(f)">
              <component :is="shown[f] != null ? EyeOff : Eye" :size="15" /></button>
            <button class="btn icon" :disabled="sealed" title="Copy (written to the history)" :aria-label="`Copy ${f}`" @click="copy(f)"><CopyIcon :size="15" /></button>
          </template>
        </div>
      </div>
      <p v-if="!e.can_reveal" class="hint">You see the names; revealing needs secret:apply here.</p>
      <p v-else-if="sealed" class="hint">The vault is sealed: nothing can be revealed until an administrator unseals it.</p>
      <details v-if="e.versions?.length > 1" class="small">
        <summary class="muted">Versions ({{ e.versions.length }}; the last 10 are kept)</summary>
        <ul class="plain"><li v-for="v in e.versions" :key="v.version">{{ v.version }} · {{ v.by }} · {{ stamp(v.at) }}
          <span v-for="f in v.fields" :key="f" class="vf">
            <button v-if="e.can_reveal && v.version !== e.version" class="linkish mono" :disabled="sealed" :aria-label="`Reveal ${f} of version ${v.version}`" @click="revealOld(v.version, f)">{{ f }}</button>
            <span v-else class="mono faint">{{ f }}</span><span v-if="old[`${v.version}:${f}`] != null" class="mono">: {{ old[`${v.version}:${f}`] }}</span></span></li></ul>
      </details>
    </template>

    <template #foot>
      <template v-if="editing">
        <button class="btn" @click="e ? (editing = false) : emit('close')">Cancel</button>
        <button class="btn primary" :disabled="busy || !form?.path" @click="save">{{ e ? 'Save' : 'Create' }}</button>
      </template>
      <template v-else-if="e?.can_edit">
        <button class="btn danger left" @click="remove">Delete</button>
        <button class="btn" @click="startEdit"><KeyRound :size="14" /> Edit</button>
      </template>
    </template>
  </Modal>

  <Modal v-if="pw" title="Confirm your password" @close="pw = null">
    <p class="small muted m-0">Revealing a secret asks again every few minutes.</p>
    <Field label="Password" :error="pw.error"><input v-model="pw.password" type="password" autocomplete="current-password" @keydown.enter="sendPassword"></Field>
    <template #foot>
      <button class="btn" @click="pw = null">Cancel</button>
      <button class="btn primary" :disabled="!pw.password" @click="sendPassword">Confirm</button>
    </template>
  </Modal>
</template>

<style scoped>
.flabel { font-size: calc(12px * var(--text)); font-weight: 600; color: var(--muted); margin-top: 4px; }
.frow { display: grid; grid-template-columns: 160px minmax(0, 1fr) auto auto auto; gap: 6px; align-items: center; }
.fields { display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: var(--r-box); }
.fld { display: grid; grid-template-columns: 160px minmax(0, 1fr) auto auto; gap: 8px; align-items: center; padding: 8px 12px; border-top: 1px solid var(--line); }
.fld:first-child { border-top: 0; }
.fname { color: var(--muted); }
.fval { overflow-wrap: anywhere; user-select: all; }
.vf { margin-left: 8px; }
.linkish { background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; text-decoration: underline dotted; font-size: inherit; }
.fval.hidden { color: var(--faint); user-select: none; letter-spacing: 1px; }
@media (max-width: 640px) {
  .frow { grid-template-columns: minmax(0, 1fr) auto auto auto; }
  .fld { grid-template-columns: minmax(0, 1fr) auto auto; }
  .frow > :first-child, .fname { grid-column: 1 / -1; }
}
</style>
