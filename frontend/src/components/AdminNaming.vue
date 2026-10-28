<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, ApiError, enc } from '../api'
import { changed, state, toast } from '../store'
import Field from './Field.vue'
import Modal from './Modal.vue'
import { confirm } from '../confirm'
import { Pencil, Plus, Trash2 } from 'lucide-vue-next'

const props = defineProps({ q: { type: String, default: '' } })
const parts = ref(null)
const LIMIT = 12
const more = reactive({ category: false, type: false, role: false })
const needle = computed(() => props.q.trim().toLowerCase())
const list = (kind) => (parts.value?.[kind] || []).filter((p) => !needle.value || `${p.code} ${p.label} ${p.description || ''}`.toLowerCase().includes(needle.value))
const shown = (kind) => (more[kind] || needle.value ? list(kind) : list(kind).slice(0, LIMIT))
watch(needle, () => Object.assign(more, { category: false, type: false, role: false }))
const KINDS = [['category', 'Categories', 'what it is for: srv, net, cli'], ['type', 'Types', 'what it is: vm, pm, ct'], ['role', 'Roles', 'what it does: dns, k3s, mon. Free text in the host form; these are suggestions with a label.']]
const busy = ref(false)
const NOUN = { category: 'category', type: 'type', role: 'role' }
const editing = ref(null) // {kind, code, label, description, _orig}
const errors = ref({})

onMounted(async () => { parts.value = await api.get('/api/naming') })

async function call(fn, msg) {
  errors.value = {}
  try { parts.value = await fn(); toast(msg); changed(); return true } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error'); return false
  }
}
function open(kind, p) { errors.value = {}; editing.value = p ? { ...p, kind, _orig: p.code } : { kind, code: '', label: '', description: '', _orig: null } }
async function saveEdit() {
  const { kind, _orig, ...body } = editing.value
  busy.value = true
  const ok = await call(() => (_orig ? api.put(`/api/naming/${kind}/${enc(_orig)}`, body) : api.post(`/api/naming/${kind}`, body)), `Saved ${body.code}`)
  busy.value = false
  if (ok) editing.value = null
}
async function del(kind, code) {
  if (await confirm({ title: `Delete the ${NOUN[kind]} ${code}?`, action: 'Delete', danger: true })) call(() => api.del(`/api/naming/${kind}/${enc(code)}`), `Deleted ${code}`)
}
</script>

<template>
  <div v-if="parts">
    <p class="muted small lead">Hosts are named <code>&lt;category&gt;-&lt;type&gt;-&lt;role&gt;-&lt;nn&gt;</code>, e.g. <code>srv-vm-dns-01</code>. The host form offers categories and types from these lists, takes any role you type (suggesting the ones below and those already in use) and picks the next free number. Codes used by hosts can't be renamed or deleted; labels can change any time.</p>
    <div class="cols">
      <section v-for="[kind, title, hint] in KINDS" :key="kind">
        <div class="sect-head">
          <h3>{{ title }} <span class="faint small">{{ parts[kind].length }}</span></h3>
          <span class="grow"></span>
          <button v-if="state.meta.is_admin" class="btn small" @click="open(kind)"><Plus :size="14" /> Add</button>
        </div>
        <p class="hint chint">{{ hint }}</p>
        <div class="list">
          <div v-for="p in shown(kind)" :key="p.code" class="li tight">
            <span class="mono code">{{ p.code }}</span>
            <div class="li-main"><span class="muted">{{ p.label }}</span></div>
            <div v-if="state.meta.is_admin" class="li-actions">
              <button class="btn icon" :aria-label="`Edit ${p.code}`" title="Edit" @click="open(kind, p)"><Pencil :size="14" /></button>
              <button class="btn icon" :aria-label="`Delete ${p.code}`" title="Delete" @click="del(kind, p.code)"><Trash2 :size="14" /></button>
            </div>
          </div>
          <div v-if="!list(kind).length" class="list-empty">{{ needle ? 'Nothing matches.' : 'None yet.' }}</div>
          <a v-if="shown(kind).length < list(kind).length" href="#" class="li tight small" @click.prevent="more[kind] = true">Show all {{ list(kind).length }}</a>
        </div>
        <div v-if="kind === 'role' && parts.roles_in_use?.length" class="small faint inuse">
          In use without a label: <span v-for="r in parts.roles_in_use" :key="r" class="mono">{{ r }}</span>
        </div>
      </section>
    </div>

    <Modal v-if="editing" :title="editing._orig ? `Edit ${editing._orig}` : `New ${NOUN[editing.kind]}`" :busy="busy" @close="editing = null">
      <form id="part-form" @submit.prevent="saveEdit">
        <div class="g">
          <Field label="Code" :error="errors.code"><input v-model.trim="editing.code" class="mono" placeholder="dns" required></Field>
          <Field label="Label" :error="errors.label"><input v-model="editing.label" placeholder="Name server"></Field>
        </div>
        <Field label="Description" class="mt-4"><input v-model="editing.description"></Field>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="editing = null">Cancel</button>
        <button form="part-form" class="btn primary" :disabled="busy">{{ editing._orig ? 'Save' : 'Add' }}</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.lead { margin: 0 0 18px; max-width: 90ch; }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; align-items: start; }
.sect-head { margin-bottom: 2px; }
.chint { margin: 0 0 8px; }
.list > .li.tight { padding: 7px 12px; align-items: center; }
.code { width: 64px; flex: none; }
.inuse { margin-top: 8px; }
.inuse .mono { margin-right: 6px; }
.g { display: grid; grid-template-columns: 1fr 2fr; gap: 12px; }
</style>
