<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, ApiError } from '../api'
import { useQuery } from '../query'
import Copy from '../components/Copy.vue'
import Field from '../components/Field.vue'
import Pager from '../components/Pager.vue'
import FilterSelect from '../components/FilterSelect.vue'
import Modal from '../components/Modal.vue'
import { confirm } from '../confirm'
import { Pencil, Plus } from 'lucide-vue-next'
import { can, changed, state, toast } from '../store'

const query = useQuery('/api/aliases', { onError: (e) => toast(e.message, 'error') })
const all = computed(() => query.data.value || [])
const route = useRoute()
const q = ref(String(route.query.q || ''))
watch(() => route.query.q, (v) => { if (v !== undefined) q.value = String(v) }) // from the search palette
const kind = ref(null)
const editing = ref(null) // null | 'new' | alias name
const form = reactive({ name: '', fw_id: '', type: 'host', members: '', description: '' })
const errors = ref({})
const busy = ref(false)

const load = () => query.refresh()

const KINDS = { network: 'Networks', zone: 'Zones', area: 'Areas', host: 'Hosts', custom: 'Custom' }
const max = computed(() => Number(state.meta.settings.alias_max_length || 32))
const visible = computed(() => {
  const n = q.value.trim().toLowerCase()
  return all.value.filter((a) => (!kind.value || a.kind === kind.value) &&
    (!n || a.name.toLowerCase().includes(n) || a.fw_id.toLowerCase().includes(n) || a.content.join(' ').toLowerCase().includes(n) || a.description.toLowerCase().includes(n)))
})
const page = ref(0)
const SIZE = 50
watch([q, kind], () => { page.value = 0 })
const paged = computed(() => visible.value.slice(page.value * SIZE, (page.value + 1) * SIZE))
const counts = computed(() => Object.fromEntries(Object.keys(KINDS).map((k) => [k, all.value.filter((a) => a.kind === k).length])))

async function edit(a) {
  errors.value = {}
  if (!a) {
    Object.assign(form, { name: '', fw_id: '', type: 'host', members: '', description: '' })
    editing.value = 'new'
    return
  }
  const custom = await api.get('/api/aliases/custom')
  const c = custom.find((x) => x.name === a.name)
  Object.assign(form, { name: c.name, fw_id: c.fw_id || '', type: c.type, members: c.members.join('\n'), description: c.description })
  editing.value = c.name
}

async function save() {
  busy.value = true
  errors.value = {}
  const body = { ...form, members: form.members.split('\n').map((m) => m.trim()).filter(Boolean) }
  try {
    if (editing.value === 'new') await api.post('/api/aliases', body)
    else await api.put(`/api/aliases/${encodeURIComponent(editing.value)}`, body)
    toast(`Saved ${body.name}`)
    editing.value = null
    await Promise.all([load(), changed()])
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    else toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}

async function remove() {
  if (!(await confirm({ title: `Delete alias ${editing.value}?`, body: 'Rules on the firewall that use it break on the next sync.', action: 'Delete', danger: true }))) return
  try {
    await api.del(`/api/aliases/${encodeURIComponent(editing.value)}`)
    toast(`Deleted ${editing.value}`)
    editing.value = null
    await Promise.all([load(), changed()])
  } catch (e) {
    toast(e.message, 'error')
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Firewall aliases</h1>
      <span class="sub">{{ visible.length }} of {{ all.length }}</span>
      <span class="grow"></span>
      <input v-model="q" type="search" placeholder="Search names and contents" style="max-width: 300px" aria-label="Search aliases">
      <button v-if="can('alias', 'edit')" class="btn primary" @click="edit(null)"><Plus :size="15" /> Custom alias</button>
    </div>

    <Modal v-if="editing" wide :busy="busy" :title="editing === 'new' ? 'New custom alias' : `Edit ${editing}`" @close="editing = null">
      <div class="grid2">
        <Field label="Name" :error="errors.name" hint="Anything readable, e.g. “Hosts that may administer Proxmox”. It goes into the firewall description.">
          <input v-model.trim="form.name" type="text" :class="{ invalid: errors.name }">
        </Field>
        <Field label="Firewall id" :error="errors.fw_id" :hint="`At most ${max} characters (letters, digits, _). Empty = ${state.meta.settings.alias_ids === 'readable' ? 'the name if it fits, else a shortened form' : 'a short uid like ldx_3fa9c201'}.`">
          <input v-model.trim="form.fw_id" type="text" class="mono" :class="{ invalid: errors.fw_id }" placeholder="HG_SPECIAL_ADMIN" :maxlength="max">
        </Field>
      </div>
      <div class="grid2 mt-4">
        <Field label="Type" :error="errors.type">
          <select v-model="form.type"><option v-for="t in state.meta.alias_types" :key="t">{{ t }}</option></select>
        </Field>
        <Field label="Description"><input v-model="form.description" type="text"></Field>
      </div>
      <div class="grid2 mt-4">
        <Field label="Members, one per line" :error="errors.members">
          <textarea v-model="form.members" rows="8" class="mono" placeholder="host:srv-vm-dns-01&#10;network:mgmt&#10;zone:DC_TC&#10;alias:HG_ZONE_CA_ST_ADMIN&#10;10.0.0.0/8"></textarea>
        </Field>
        <div class="small muted refs">
          <div class="rh">Member references</div>
          <dl class="kv">
            <dt><code>alias:name</code></dt><dd>another alias, by its name or firewall id</dd>
            <dt><code>host:name</code></dt><dd>the host's alias (or its MACs in a <code>mac</code> alias); fun names work too</dd>
            <dt><code>network:name</code></dt><dd>the network's alias</dd>
            <dt><code>zone:DC_TC</code></dt><dd>all networks of a zone</dd>
            <dt><code>area:DC</code></dt><dd>all networks of an area</dd>
            <dt>anything else</dt><dd>used as written: address, CIDR, port, MAC, URL</dd>
          </dl>
        </div>
      </div>
      <template #foot>
        <button v-if="editing !== 'new'" class="btn danger" @click="remove">Delete</button>
        <span class="grow"></span>
        <button class="btn ghost" @click="editing = null">Cancel</button>
        <button class="btn primary" :disabled="busy" @click="save">{{ busy ? 'Saving…' : 'Save alias' }}</button>
      </template>
    </Modal>

    <div class="filters">
      <FilterSelect v-model="kind" label="Kind" :options="Object.entries(KINDS).map(([k, l]) => ({ value: k, label: `${l} (${counts[k]})` }))" />
    </div>

    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Alias</th><th title="The alias's name on the firewall">Firewall id</th><th>Type</th><th>Contents</th><th>Description</th><th title="Generated from a network, zone, area or host; or custom">Source</th></tr></thead>
          <tbody>
            <tr v-for="a in paged" :key="a.name">
              <td class="nowrap mono small">{{ a.name }}</td>
              <td class="nowrap"><Copy :text="a.fw_id" /></td>
              <td class="muted nowrap">{{ a.type }}</td>
              <td>
                <span class="mono small">{{ a.content.join(', ') || '—' }}</span>
                <div v-for="e in a.errors" :key="e" class="err">{{ e }}</div>
              </td>
              <td class="muted small">{{ a.description }}</td>
              <td class="right">
                <button v-if="a.can_edit" class="btn icon" title="Edit alias" aria-label="Edit alias" @click="edit(a)"><Pencil :size="15" /></button>
                <span v-else class="faint small" title="Generated from the inventory">{{ a.kind }}</span>
              </td>
            </tr>
            <tr v-if="!visible.length"><td colspan="6" class="empty">Nothing matches.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <Pager v-model="page" :total="visible.length" :size="SIZE" />
  </div>
</template>

<style scoped>
.refs { padding-top: 2px; }
.rh { font-weight: 500; color: var(--ink); margin-bottom: 6px; }
</style>
