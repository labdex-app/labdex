<script setup>
// Sites: the parts of one infrastructure (home behind the firewall, public at a cloud provider).
// Each has its own hosts, networks and integrations; zones, naming and people are shared.
import { onMounted, reactive, ref } from 'vue'
import { api, ApiError, enc } from '../api'
import { changed, loadMeta, toast } from '../store'
import Field from './Field.vue'
import Modal from './Modal.vue'
import ColourPicker from './ColourPicker.vue'
import { confirm } from '../confirm'
import { Download, Pencil, Plus, Trash2 } from 'lucide-vue-next'

const data = ref(null)
const errors = ref({})
const busy = ref(false)
const open = ref(false)
const blank = () => ({ code: '', name: '', description: '', colour: '', _orig: null })
const form = reactive(blank())
const cleanCode = (t) => String(t || '').trim().toLowerCase().replace(/[\s_.]+/g, '-').replace(/[^a-z0-9-]/g, '').replace(/^-+|-+$/g, '')

async function load() { data.value = await api.get('/api/sites') }
onMounted(() => load().catch((e) => toast(e.message, 'error')))

async function call(fn, msg) {
  errors.value = {}
  try {
    const r = await fn()
    data.value = { ...data.value, ...r }
    toast(msg)
    await Promise.all([changed(), loadMeta()])
    return true
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
    return false
  }
}
function edit(s) { errors.value = {}; Object.assign(form, { ...blank(), ...s, _orig: s.code }); open.value = true }
function add() { errors.value = {}; Object.assign(form, blank()); open.value = true }
async function save() {
  const { _orig, usage: _usage, ...rest } = form
  const body = { ...rest, code: cleanCode(rest.code) }
  busy.value = true
  const ok = await call(() => (_orig ? api.put(`/api/sites/${enc(_orig)}`, body) : api.post('/api/sites', body)), `Saved site ${body.code}`)
  busy.value = false
  if (ok) open.value = false
}
async function del(s) {
  const ok = await confirm({ title: `Delete site ${s.code}?`, body: 'Only an empty site can go: move or delete its hosts, networks and integrations first.', action: 'Delete', danger: true })
  if (ok) call(() => api.del(`/api/sites/${enc(s.code)}`), `Deleted ${s.code}`)
}
async function move(i, d) {
  const order = data.value.sites.map((s) => s.code)
  const j = i + d
  if (j < 0 || j >= order.length) return
  ;[order[i], order[j]] = [order[j], order[i]]
  await call(() => api.put('/api/sites/order', { order }), 'Saved the order')
}
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`
</script>

<template>
  <div v-if="data" class="sites">
    <div class="sect-head">
      <p class="muted small lead">
        A <b>site</b> is a part of your infrastructure with its own hosts, networks, integrations and credentials:
        <code>home</code> behind your firewall, <code>public</code> at a cloud provider. Zones, naming rules, features, people and
        notifications are shared. An integration works in one site, so the home firewall never gets public nodes unless a custom alias names them.
      </p>
      <span class="grow"></span>
      <button v-if="data.can_edit" class="btn primary" @click="add"><Plus :size="15" /> Site</button>
    </div>

    <div class="list">
      <div v-for="(s, i) in data.sites" :key="s.code" class="li">
        <span class="swatch" :style="{ background: s.colour || 'var(--line)' }"></span>
        <div class="li-main">
          <div class="li-title"><span class="mono">{{ s.code }}</span> {{ s.name }}
            <span v-if="s.code === data.default" class="faint small">new hosts go here</span></div>
          <div v-if="s.description" class="li-sub">{{ s.description }}</div>
        </div>
        <div class="meta small">
          <RouterLink :to="{ path: '/hosts', query: { site: s.code } }">{{ plural(s.usage.hosts, 'host') }}</RouterLink>
          <RouterLink :to="{ path: '/networks', query: { site: s.code } }">{{ plural(s.usage.networks, 'network') }}</RouterLink>
          <span>{{ plural(s.usage.integrations, 'integration') }}</span>
        </div>
        <div v-if="data.can_edit" class="li-actions">
          <button class="btn icon" title="Move up" aria-label="Move up" :disabled="i === 0" @click="move(i, -1)">↑</button>
          <a class="btn icon" :href="`/api/sites/${enc(s.code)}/export?download=1`" title="Download the site's YAML" aria-label="Download the site's YAML"><Download :size="15" /></a>
          <button class="btn icon" title="Edit site" aria-label="Edit site" @click="edit(s)"><Pencil :size="15" /></button>
          <button class="btn icon" title="Delete site" aria-label="Delete site" :disabled="data.sites.length < 2" @click="del(s)"><Trash2 :size="15" /></button>
        </div>
      </div>
    </div>
    <p class="muted small">
      Each site's networks and hosts can live in git as <code>labdex/&lt;site&gt;.yaml</code>:
      <code>labdex export --site public -o labdex/public.yaml</code>, and back with <code>labdex import labdex/public.yaml</code>,
      which replaces only that site.
    </p>

    <Modal v-if="open" :title="form._orig ? `Edit site ${form._orig}` : 'New site'" :busy="busy" @close="open = false">
      <form id="site-form" class="fstack" @submit.prevent="save">
        <div class="grid2">
          <Field label="Code" :error="errors.code" :hint="form._orig ? 'A site keeps its code' : 'Lowercase, used in selectors (site:public) and file names'">
            <input v-model="form.code" class="mono" placeholder="public" :disabled="!!form._orig" required></Field>
          <Field label="Name" :error="errors.name"><input v-model.trim="form.name" placeholder="Public"></Field>
        </div>
        <Field label="Colour" :error="errors.colour"><ColourPicker v-model="form.colour" /></Field>
        <Field label="Description"><input v-model="form.description" placeholder="Contabo nodes and Cloudflare records"></Field>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="open = false">Cancel</button>
        <button form="site-form" class="btn primary" :disabled="busy">{{ form._orig ? 'Save site' : 'Add site' }}</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.sites > * + * { margin-top: 18px; }
.lead { margin: 0; max-width: 78ch; }
.swatch { width: 12px; height: 12px; border-radius: var(--r-sm); flex: none; margin-right: 10px; }
.meta { display: flex; gap: 16px; color: var(--muted); white-space: nowrap; }
.fstack > * + * { margin-top: 14px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 700px) { .grid2 { grid-template-columns: minmax(0, 1fr); } .meta { display: none; } }
</style>
