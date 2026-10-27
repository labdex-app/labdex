<script setup>
// Tags known everywhere: colour, description, rename (follows every host and role scope), delete.
import { computed, ref } from 'vue'
import { api, ApiError, enc } from '../api'
import { useQuery } from '../query'
import { changed, state, toast } from '../store'
import Pager from './Pager.vue'
import ColourPicker from './ColourPicker.vue'
import Field from './Field.vue'
import Modal from './Modal.vue'
import TagChip from './TagChip.vue'
import { confirm } from '../confirm'
import { Pencil, Plus, Trash2 } from 'lucide-vue-next'

const props = defineProps({ q: { type: String, default: '' } })
const query = useQuery('/api/tags', { onError: (e) => toast(e.message, 'error') })
const tags = computed(() => query.data.value || [])
const needle = computed(() => props.q.trim().toLowerCase())
const filtered = computed(() => tags.value.filter((t) => !needle.value || `${t.name} ${t.description}`.toLowerCase().includes(needle.value)))
const page = ref(0)
const SIZE = 25
const paged = computed(() => filtered.value.slice(page.value * SIZE, (page.value + 1) * SIZE))
const edit = ref(null)
const errors = ref({})
const busy = ref(false)
const open = (t) => { errors.value = {}; edit.value = t ? { ...t, _orig: t.name } : { name: '', colour: '', description: '', _orig: null } }

async function save() {
  const { _orig: orig, hosts: _hosts, ...t } = edit.value // hosts: a count to show, not a field
  errors.value = {}
  busy.value = true
  try {
    if (orig) await api.put(`/api/tags/${enc(orig)}`, t)
    else await api.post('/api/tags', t)
    toast(`Saved ${t.name}`)
    edit.value = null
    await query.refresh()
    await changed()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
  } finally { busy.value = false }
}
async function remove(t) {
  if (!(await confirm({ title: `Delete the tag ${t.name}?`, body: t.hosts ? `It's removed from ${t.hosts} host${t.hosts === 1 ? '' : 's'}.` : 'No host uses it.', action: 'Delete', danger: true }))) return
  try { await api.del(`/api/tags/${enc(t.name)}`); toast(`Deleted ${t.name}`); await query.refresh(); await changed() } catch (e) { toast(e.message, 'error') }
}
</script>

<template>
  <div>
    <div class="sect-head">
      <div>
        <p class="muted small lead">Hosts pick tags from this list; typing a new one on a host adds it here. Each tag is an Ansible group
          <code>tag_&lt;name&gt;</code> and a role scope <code>tag:&lt;name&gt;</code>.</p>
      </div>
      <span class="grow"></span>
      <button v-if="state.meta.is_admin" class="btn primary" @click="open(null)"><Plus :size="15" /> Tag</button>
    </div>
    <div class="list">
      <div v-for="t in paged" :key="t.name" class="li mid">
        <div class="li-main">
          <div class="li-title"><TagChip :name="t.name" :colour="t.colour || ''" />
            <span v-if="t.description" class="small muted">{{ t.description }}</span></div>
        </div>
        <RouterLink class="small cnt" :to="`/hosts?tag=${encodeURIComponent(t.name)}`">{{ t.hosts }} host{{ t.hosts === 1 ? '' : 's' }}</RouterLink>
        <div v-if="state.meta.is_admin" class="li-actions">
          <button class="btn icon" title="Edit tag" aria-label="Edit tag" @click="open(t)"><Pencil :size="15" /></button>
          <button class="btn icon" title="Delete tag" aria-label="Delete tag" @click="remove(t)"><Trash2 :size="15" /></button>
        </div>
      </div>
      <div v-if="!filtered.length" class="list-empty">{{ tags.length ? 'Nothing matches.' : 'No tags yet. Add one here or type it on a host.' }}</div>
    </div>
    <Pager v-model="page" :total="filtered.length" :size="SIZE" />

    <Modal v-if="edit" :title="edit._orig ? `Edit tag ${edit._orig}` : 'New tag'" :busy="busy" @close="edit = null">
      <form id="tag-form" @submit.prevent="save">
        <div class="tg">
          <Field label="Name" :error="errors.name" :hint="edit._orig ? 'Renaming follows every host and role scope' : ''">
            <input v-model.trim="edit.name" class="mono" placeholder="backup" required></Field>
          <div class="pv"><TagChip :name="edit.name || 'tag'" :colour="edit.colour || ''" /></div>
        </div>
        <Field label="Colour" :error="errors.colour" style="margin-top: 14px"><ColourPicker v-model="edit.colour" none-title="no colour" /></Field>
        <Field label="Description" style="margin-top: 14px"><input v-model="edit.description" placeholder="What the tag means"></Field>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="edit = null">Cancel</button>
        <button form="tag-form" class="btn primary" :disabled="busy">{{ edit._orig ? 'Save tag' : 'Add tag' }}</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.lead { margin: 0; max-width: 78ch; }
.cnt { white-space: nowrap; color: var(--muted); align-self: center; }
.tg { display: flex; align-items: flex-end; gap: 14px; }
.tg > :first-child { flex: 1; }
.pv { padding-bottom: 8px; }
</style>
