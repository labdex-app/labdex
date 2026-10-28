<script setup>
// Fun name pools: lists to suggest fun names from, optionally tied to categories, areas or zones.
import { computed, reactive, ref, watch } from 'vue'
import { api, ApiError, enc } from '../api'
import { useQuery } from '../query'
import { changed, toast } from '../store'
import Field from './Field.vue'
import Pager from './Pager.vue'
import Modal from './Modal.vue'
import { confirm } from '../confirm'
import { Pencil, Plus, Trash2, Eye, EyeOff } from 'lucide-vue-next'

const props = defineProps({ q: { type: String, default: '' } })
const needle = computed(() => props.q.trim().toLowerCase())
const page = ref(0)
const SIZE = 15
const matches = (p) => !needle.value || `${p.name} ${p.description} ${p.applies_to.join(' ')}`.toLowerCase().includes(needle.value)
  || p.names.some((n) => n.includes(needle.value))
const filtered = computed(() => pools.value.filter(matches))
const paged = computed(() => filtered.value.slice(page.value * SIZE, (page.value + 1) * SIZE))
watch(needle, () => { page.value = 0 })
const hits = (p) => (needle.value ? p.names.filter((n) => n.includes(needle.value)) : [])

const poolsQ = useQuery('/api/naming/pools', { onError: (e) => toast(e.message, 'error') })
const pools = computed(() => poolsQ.data.value || [])
const editing = ref(null) // null | 'new' | pool name
const form = reactive({ name: '', description: '', applies_to: '', names: '', enabled: true })
const errors = ref({})
const open = ref({})

function edit(p) {
  errors.value = {}
  if (!p) {
    Object.assign(form, { name: '', description: '', applies_to: '', names: '', enabled: true })
    editing.value = 'new'
    return
  }
  Object.assign(form, { name: p.name, description: p.description, applies_to: p.applies_to.join(', '), names: p.names.join('\n'), enabled: p.enabled })
  editing.value = p.name
}
const count = computed(() => form.names.split(/[\s,;]+/).filter(Boolean).length)

async function save() {
  errors.value = {}
  const body = { ...form }
  try {
    if (editing.value === 'new') await api.post('/api/naming/pools', body)
    else await api.put(`/api/naming/pools/${enc(editing.value)}`, body)
    toast(`Saved pool ${form.name}`)
    editing.value = null
    await changed()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
  }
}
async function toggle(p) {
  try {
    await api.put(`/api/naming/pools/${enc(p.name)}`, { ...p, enabled: !p.enabled })
    await changed()
  } catch (e) { toast(e.message, 'error') }
}
async function remove(p) {
  if (!(await confirm({ title: `Delete the pool ${p.name}?`, body: 'Hosts keep the names they have.', action: 'Delete', danger: true }))) return
  try { await api.del(`/api/naming/pools/${enc(p.name)}`); toast(`Deleted ${p.name}`); await changed() } catch (e) { toast(e.message, 'error') }
}
</script>

<template>
  <div>
    <div class="sect-head">
      <div>
        <p class="muted small lead">The host form suggests unused names from these. A pool that applies to the host's category, area or zone is asked first; with none, all enabled pools are used.</p>
      </div>
      <span class="grow"></span>
      <button class="btn primary" @click="edit(null)"><Plus :size="15" /> Pool</button>
    </div>

    <div class="list">
      <template v-for="p in paged" :key="p.name">
        <div class="li" :class="{ off: !p.enabled }">
          <div class="li-main">
            <div class="li-title"><span class="mono">{{ p.name }}</span>
              <span v-if="p.builtin" class="tag">built-in</span>
              <span v-if="!p.enabled" class="tag">disabled</span></div>
            <div class="li-sub">
              <span v-if="p.description">{{ p.description }}</span>
              <span>applies to <span class="mono">{{ p.applies_to.join(', ') || 'anything unmatched' }}</span></span>
              <a href="#" @click.prevent="open[p.name] = !open[p.name]">{{ p.names.length }} names, {{ p.free }} unused</a>
            </div>
            <div v-if="hits(p).length && !open[p.name]" class="small hitrow">matches: <span v-for="n in hits(p).slice(0, 8)" :key="n" class="nchip">{{ n }}</span><span v-if="hits(p).length > 8" class="faint"> +{{ hits(p).length - 8 }}</span></div>
            <div v-if="open[p.name]" class="names">
              <span v-for="n in (needle ? p.names.filter((x) => x.includes(needle)) : p.names)" :key="n" class="nchip" :class="{ used: (p.used || []).includes(n) }">{{ n }}</span>
            </div>
          </div>
          <div class="li-actions">
            <button class="btn icon" :title="p.enabled ? 'Disable' : 'Enable'" :aria-label="p.enabled ? 'Disable pool' : 'Enable pool'" @click="toggle(p)">
              <component :is="p.enabled ? EyeOff : Eye" :size="15" /></button>
            <button class="btn icon" title="Edit pool" aria-label="Edit pool" @click="edit(p)"><Pencil :size="15" /></button>
            <button class="btn icon" title="Delete pool" aria-label="Delete pool" @click="remove(p)"><Trash2 :size="15" /></button>
          </div>
        </div>
      </template>
      <div v-if="!filtered.length" class="list-empty">{{ pools.length ? 'Nothing matches.' : 'No pools.' }}</div>
    </div>
    <Pager v-model="page" :total="filtered.length" :size="SIZE" />

    <Modal v-if="editing" wide :title="editing === 'new' ? 'New pool' : `Edit pool ${editing}`" @close="editing = null">
      <form id="pool-form" @submit.prevent="save">
        <div class="grid2">
          <Field label="Name" :error="errors.name"><input v-model.trim="form.name" class="mono" placeholder="pokemon" required></Field>
          <Field label="Applies to" :error="errors.applies_to" hint="category:srv, area:CA, zone:DC_*, * — empty: only when nothing else matches">
            <input v-model.trim="form.applies_to" class="mono" placeholder="category:srv, zone:DC_*">
          </Field>
        </div>
        <Field label="Description" :error="errors.description" class="mt-4"><input v-model="form.description" type="text"></Field>
        <Field :label="`Names (${count})`" :error="errors.names" hint="Paste a list: spaces, commas or new lines between names. Lowercase letters, digits and dashes." class="mt-4">
          <textarea v-model="form.names" rows="8" class="mono" spellcheck="false" placeholder="arcanine&#10;snorlax&#10;umbreon"></textarea>
        </Field>
        <label class="check mt-3"><input v-model="form.enabled" type="checkbox"> Enabled</label>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="editing = null">Cancel</button>
        <button form="pool-form" class="btn primary">{{ editing === 'new' ? 'Create pool' : 'Save pool' }}</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.lead { margin: 0; max-width: 78ch; }
.li.off .li-title { opacity: .6; }
.hitrow { margin-top: 6px; }
.names { margin-top: 8px; line-height: 2; }
.nchip { display: inline-block; font-family: var(--mono); font-size: calc(12px * var(--text)); padding: 0 7px; margin: 0 4px 4px 0; border-radius: 999px; background: var(--raise); color: var(--muted); }
.nchip.used { opacity: .45; text-decoration: line-through; }
</style>
