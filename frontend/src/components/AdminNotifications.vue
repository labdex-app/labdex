<script setup>
// Notification channels (ntfy, Matrix, email, webhook) and what was sent lately.
import { confirm } from '../confirm'
import Modal from './Modal.vue'
import { Pencil, Plus, Send, Trash2 } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { api, ApiError } from '../api'
import Field from './Field.vue'
import SettingsForm from './SettingsForm.vue'
import { ago, state, toast } from '../store'

const channels = ref([])
const kinds = ref({})
const events = ref([])
const recent = ref([])
const edit = ref(null)
const openMsg = ref({})
const errors = ref({})
const busy = ref(false)
const testing = ref(null)

async function load() {
  const [c, n] = await Promise.all([api.get('/api/notification-channels'), api.get('/api/notifications?limit=30')])
  channels.value = c.channels
  kinds.value = c.kinds
  events.value = c.events
  recent.value = n
}
onMounted(() => load().catch((e) => toast(e.message, 'error')))

function start(ch, kind = 'ntfy') {
  errors.value = {}
  if (ch) {
    edit.value = { ...ch, settings: { ...ch.settings }, events: [...ch.events], integrations: ch.integrations.join(', '), extra: ch.events.filter((e) => !events.value.some((x) => x.event === e)).join(', ') }
  } else {
    const settings = Object.fromEntries((kinds.value[kind]?.fields || []).map((f) => [f.key, f.default ?? (f.type === 'list' ? [] : '')]))
    edit.value = { name: '', kind, enabled: true, settings, events: ['plan.waiting', 'job.failed', 'backup.failed'], integrations: '*', extra: '', secrets_set: [], secret_refs: {} }
  }
}
function setKind(k) { start(null, k); }
function toggle(e) { const i = edit.value.events.indexOf(e); i < 0 ? edit.value.events.push(e) : edit.value.events.splice(i, 1) }
async function save() {
  busy.value = true
  errors.value = {}
  const e = edit.value
  const known = e.events.filter((x) => events.value.some((k) => k.event === x))
  const d = { name: e.name, kind: e.kind, enabled: e.enabled, settings: e.settings,
              events: [...known, ...e.extra.split(/[\s,]+/).filter(Boolean)], integrations: e.integrations.split(/[\s,]+/).filter(Boolean) }
  try {
    if (e.id) await api.put(`/api/notification-channels/${e.id}`, d)
    else await api.post('/api/notification-channels', d)
    edit.value = null
    toast('Saved')
    await load()
  } catch (err) {
    if (err instanceof ApiError && err.errors.length) errors.value = Object.fromEntries(err.errors.map((x) => [x.field, x.message]))
    else toast(err.message, 'error')
  } finally {
    busy.value = false
  }
}
async function remove(ch) {
  if (!(await confirm({ title: `Delete channel ${ch.name}?`, action: 'Delete', danger: true }))) return
  try { await api.del(`/api/notification-channels/${ch.id}`); await load() } catch (e) { toast(e.message, 'error') }
}
async function test(ch) {
  testing.value = ch.id
  try {
    await api.post(`/api/notification-channels/${ch.id}/test`)
    toast(`Sent a test to ${ch.name}`)
  } catch (e) {
    toast(`${ch.name}: ${e.message}`, 'error')
  } finally {
    testing.value = null
    load()
  }
}
const sub = (prefix) => Object.fromEntries(Object.entries(errors.value).filter(([k]) => k.startsWith(prefix)).map(([k, v]) => [k.slice(prefix.length), v]))
const target = (c) => c.settings.topic || c.settings.room || c.settings.url || (c.settings.to || []).join(', ')
</script>

<template>
  <div class="stack">
    <div class="sect-head">
      <div>
        <h2>Notification channels</h2>
        <p class="muted small lead">Plans waiting for approval, failed actions and backups, hosts going down: sent to ntfy, a Matrix room, email or a webhook. Messages that can't be delivered are retried for about an hour.</p>
      </div>
      <span class="grow"></span>
      <button type="button" class="btn primary" @click="start(null)"><Plus :size="15" /> Channel</button>
    </div>
    <p v-if="!state.meta.settings.public_url" class="warnbox small">Set “Address of this labdex” under <RouterLink to="/admin/general">General</RouterLink> so messages link back here.</p>
    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Channel</th><th>Sends</th><th>Last</th><th></th></tr></thead>
          <tbody>
            <tr v-for="c in channels" :key="c.id" :class="{ faint: !c.enabled }">
              <td><b>{{ c.name }}</b> <span class="chip small">{{ kinds[c.kind]?.label }}</span><div class="small faint mono ell">{{ target(c) }}</div></td>
              <td class="small mono">{{ c.events.join(', ') }}<div v-if="c.integrations.join() !== '*'" class="faint">only {{ c.integrations.join(' ') }}</div></td>
              <td class="small">
                <template v-if="c.last"><span :class="{ 'c-red': c.last.status === 'failed', 'c-green': c.last.status === 'sent' }">{{ c.last.status }}</span> {{ c.last.event }} · {{ ago(c.last.at) }}</template>
                <span v-else class="faint">nothing yet</span>
                <div v-if="c.failed" class="c-red">{{ c.failed }} failed</div>
              </td>
              <td class="right nowrap">
                <button class="btn small" :disabled="testing === c.id" @click="test(c)"><Send :size="13" /> {{ testing === c.id ? 'Sending…' : 'Test' }}</button>
                <button class="btn icon" title="Edit channel" aria-label="Edit channel" @click="start(c)"><Pencil :size="15" /></button>
                <button class="btn icon" title="Delete channel" aria-label="Delete channel" @click="remove(c)"><Trash2 :size="15" /></button>
              </td>
            </tr>
            <tr v-if="!channels.length"><td colspan="4" class="empty">No channels yet.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <Modal v-if="edit" wide :busy="busy" :title="edit.id ? `Edit ${edit.name}` : 'New channel'" @close="edit = null">
    <form id="chan-form" @submit.prevent="save">
      <div class="cols">
        <div class="stack">
          <div v-if="!edit.id" class="seg" role="group" aria-label="Kind">
            <button v-for="(k, id) in kinds" :key="id" type="button" :class="{ on: edit.kind === id }" @click="setKind(id)">{{ k.label }}</button>
          </div>
          <Field label="Name" :error="errors.name"><input v-model.trim="edit.name" type="text" required></Field>
          <SettingsForm :fields="kinds[edit.kind].fields" v-model:values="edit.settings" :errors="sub('settings.')"
                        :secrets-set="edit.secrets_set" :secret-refs="edit.secret_refs" />
        </div>
        <div class="stack">
          <Field label="Send" :error="errors.events">
            <div class="evs">
              <label v-for="e in events" :key="e.event" class="check tick small"><input type="checkbox" :checked="edit.events.includes(e.event)" @change="toggle(e.event)">
                {{ e.label }} <code class="faint">{{ e.event }}</code></label>
            </div>
          </Field>
          <Field label="Other events" hint="Patterns, e.g. plugin.* for what plugins send"><input v-model="edit.extra" type="text" class="mono" placeholder="plugin.*"></Field>
          <Field label="Only for integrations" hint="Names or patterns; * = all" :error="errors.integrations"><input v-model="edit.integrations" type="text" class="mono"></Field>
          <label class="check"><input v-model="edit.enabled" type="checkbox"> Enabled</label>
        </div>
      </div>
    </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="edit = null">Cancel</button>
        <button form="chan-form" class="btn primary" :disabled="busy">Save channel</button>
      </template>
    </Modal>

    <div class="card flush">
      <div class="card-head"><h3>Recently sent</h3></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>When</th><th>Channel</th><th>Event</th><th>Message</th><th>Status</th></tr></thead>
          <tbody>
            <tr v-for="n in recent" :key="n.id">
              <td class="small nowrap">{{ ago(n.created_at) }}</td>
              <td class="small">{{ n.channel }}</td>
              <td class="small mono">{{ n.event }}</td>
              <td class="small"><b>{{ n.title }}</b><div class="faint clamp" :class="{ open: openMsg[n.id] }">{{ n.body }}</div>
                <a v-if="(n.body || '').length > 160" href="#" class="small" @click.prevent="openMsg[n.id] = !openMsg[n.id]">{{ openMsg[n.id] ? 'less' : 'show all' }}</a></td>
              <td class="small"><span :class="{ 'c-red': n.status === 'failed', 'c-green': n.status === 'sent' }">{{ n.status }}</span>
                <span v-if="n.attempts > 1" class="faint"> ({{ n.attempts }} tries)</span><div v-if="n.error" class="c-red ell" :title="n.error">{{ n.error }}</div></td>
            </tr>
            <tr v-if="!recent.length"><td colspan="5" class="empty">Nothing sent yet.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lead { margin: 4px 0 0; max-width: 90ch; }
.card-head { padding: 14px 18px 4px; }
.card-head h3 { margin: 0; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
@media (max-width: 900px) { .cols { grid-template-columns: minmax(0, 1fr); } }
.clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; max-width: 70ch; }
.clamp.open { display: block; }
.ell { max-width: 360px; }
.evs { display: flex; flex-direction: column; gap: 4px; }
.seg { display: inline-flex; border: 1px solid var(--line-strong); border-radius: var(--r-ctl); overflow: hidden; align-self: flex-start; }
.seg button { font: inherit; font-size: calc(12.5px * var(--text)); padding: 5px 12px; background: none; border: 0; color: var(--muted); cursor: pointer; }
.seg button.on { background: var(--raise); color: var(--ink); }
</style>
