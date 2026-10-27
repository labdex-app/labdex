<script setup>
import Modal from './Modal.vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { confirm } from '../confirm'
import { computed, onMounted, reactive, ref } from 'vue'
import { api, ApiError } from '../api'
import { ago, loadNav, toast } from '../store'
import Field from './Field.vue'
import TrustBadge from './TrustBadge.vue'
import PluginInstallConfirm from './PluginInstallConfirm.vue'

const plugins = ref([])
const loadErrors = ref([])
const isolation = ref(null)
const keys = ref({ official: [], community: [] })
const market = ref(null)
const repos = ref({ repositories: [], legacy_urls: [] })
const newRepo = ref('')
const preview = ref(null)
const q = ref('')
const staged = ref(null)
const busy = ref(false)
const nk = reactive({ name: '', public_key: '' })
const keyOpen = ref(false)
const kerr = ref({})

async function load() {
  const [p, k] = await Promise.all([api.get('/api/plugins'), api.get('/api/plugins/keys')])
  plugins.value = p.plugins
  loadErrors.value = p.errors
  isolation.value = p.isolation || null
  keys.value = k
  repos.value = await api.get('/api/plugin-repos')
}
const upd = ref(null)
async function loadUpd() { try { upd.value = await api.get('/api/plugin-updates') } catch { /* */ } }
async function setMode(m) {
  try { upd.value = await api.put('/api/plugin-updates', { mode: m }); toast('Saved') } catch (e) { toast(e.message, 'error') }
}
async function checkNow() {
  busy.value = true
  try {
    const r = await api.post('/api/plugin-updates/check')
    const n = (r.announced?.length || 0) + (r.installed?.length || 0)
    toast(r.installed?.length ? `Installed ${r.installed.join(', ')}` : n ? `${n} update${n === 1 ? '' : 's'} announced` : 'Nothing new')
    await loadUpd()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
async function loadMarket() {
  try { market.value = await api.get('/api/marketplace') } catch (e) { toast(e.message, 'error') }
}
onMounted(async () => { loadUpd(); await load(); if (repos.value.repositories.length) loadMarket() })

const shown = computed(() => {
  const n = q.value.trim().toLowerCase()
  return plugins.value.filter((p) => !n || [p.id, p.name, p.description, ...(p.tags || [])].join(' ').toLowerCase().includes(n))
})

async function upload(ev) {
  const f = ev.target.files?.[0]
  if (!f) return
  busy.value = true
  const fd = new FormData()
  fd.append('file', f)
  try {
    const r = await fetch('/api/plugins/upload', { method: 'POST', body: fd, headers: { 'X-Requested-With': 'labdex' }, credentials: 'same-origin' })
    const d = await r.json()
    if (!r.ok) throw new Error(d.error || r.statusText)
    staged.value = d
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false; ev.target.value = '' }
}
async function installed() {
  staged.value = null
  await Promise.all([load(), loadNav()])
}
async function uninstall(p) {
  const upd = !!p.replaces_bundled
  if (!(await confirm({ title: upd ? `Remove the ${p.name} update?` : `Remove the plugin ${p.name}?`,
    body: upd ? `labdex goes back to the development checkout (${p.replaces_bundled}); integrations keep running.` : 'Its files are deleted.', action: 'Remove', danger: true }))) return
  try { await api.del(`/api/plugins/${p.id}`); toast(`Removed ${p.name}`); await Promise.all([load(), loadNav()]) } catch (e) { toast(e.message, 'error') }
}
async function previewRepo() {
  busy.value = true
  try { preview.value = await api.post('/api/plugin-repos/preview', { url: newRepo.value.trim() }) } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
async function trustRepo() {
  busy.value = true
  try {
    await api.post('/api/plugin-repos', { url: preview.value.url, key_id: preview.value.key_id })
    toast(`Added ${preview.value.name}`)
    preview.value = null
    newRepo.value = ''
    await load()
    await loadMarket()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
async function toggleRepo(r) {
  try { await api.put(`/api/plugin-repos/${r.id}`, { enabled: !r.enabled }); await load(); await loadMarket() } catch (e) { toast(e.message, 'error') }
}
async function removeRepo(r) {
  if (!(await confirm({ title: `Stop trusting ${r.name}?`, body: `Plugins signed only with its key stop loading.`, action: 'Stop trusting', danger: true }))) return
  try { await api.del(`/api/plugin-repos/${r.id}`); await load(); await loadMarket() } catch (e) { toast(e.message, 'error') }
}
async function addKey() {
  kerr.value = {}
  try { await api.post('/api/plugins/keys', nk); Object.assign(nk, { name: '', public_key: '' }); keyOpen.value = false; toast('Key trusted'); await load() } catch (e) {
    if (e instanceof ApiError && e.errors.length) kerr.value = e.fields
    toast(e.message, 'error')
  }
}
async function removeKey(k) {
  if (!(await confirm({ title: `Stop trusting ${k.name}?`, body: `Plugins signed only with it stop loading.`, action: 'Stop trusting', danger: true }))) return
  await api.del(`/api/plugins/keys/${k.id}`)
  await load()
}
</script>

<template>
  <div class="stack">
    <div class="row">
      <input v-model="q" type="search" placeholder="Search plugins, tags…" style="max-width: 320px" aria-label="Search plugins">
      <span class="grow"></span>
      <label class="btn primary" :class="{ disabled: busy }">Upload plugin (.zip)<input type="file" accept=".zip,application/zip" hidden @change="upload"></label>
      <button class="btn small ghost" @click="api.post('/api/plugins/reload').then(load)">Reload from disk</button>
    </div>

    <PluginInstallConfirm v-if="staged" :staged="staged" :isolation="isolation" @cancel="staged = null" @done="installed" />

    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Plugin</th><th>Version</th><th>Trust</th><th>Tags</th><th></th></tr></thead>
          <tbody>
            <tr v-for="p in shown" :key="p.id">
              <td><b>{{ p.name }}</b> <span class="tag">{{ p.kind }}</span> <span v-if="p.supported" class="tag" title="Maintained by its author">supported</span>
                <div class="small faint">{{ p.description }}</div>
                <div v-if="p.error" class="err small">{{ p.error }}</div></td>
              <td class="mono small">{{ p.version || '—' }}</td>
              <td><TrustBadge :trust="p.trust" />
                <span v-if="p.isolated" class="iso" :title="isolation?.detail">isolated</span></td>
              <td><span v-for="t in p.tags" :key="t" class="tag" style="margin-right: 4px">{{ t }}</span></td>
              <td class="right"><button v-if="!p.builtin && !p.bundled" class="btn small ghost" :title="p.replaces_bundled ? `Go back to the development checkout (${p.replaces_bundled})` : ''" @click="uninstall(p)">{{ p.replaces_bundled ? 'Remove update' : 'Remove' }}</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <p v-if="isolation" class="small iso-line" :class="`lvl-${isolation.level}`">
      <b>Isolation:</b> {{ isolation.detail }}. Official and built-in plugins run inside labdex.</p>
    <div v-if="loadErrors.length" class="errbox"><div v-for="e in loadErrors" :key="e.path" class="small"><code>{{ e.path }}</code> {{ e.error }}</div></div>

    <div v-if="upd" class="card">
      <h2>Updates</h2>
      <div class="seg" role="radiogroup" aria-label="Plugin updates">
        <button v-for="[k, l] in [['notify', 'Tell me'], ['official', 'Install official updates'], ['off', 'Off']]" :key="k" type="button" role="radio"
                :aria-checked="upd.mode === k" :class="{ on: upd.mode === k }" @click="setMode(k)">{{ l }}</button>
      </div>
      <p class="small muted" style="margin: 8px 0 0">{{ {
        notify: 'Every few hours labdex reads the repositories and sends a notification (plugins.update) for each new version once. Nothing is installed.',
        official: 'Updates signed with the labdex project key install by themselves (and are in History); anything else is announced. Held plugins and withdrawn versions are skipped.',
        off: 'No checks and no notifications. The Marketplace still shows updates when you open it.' }[upd.mode] }}</p>
      <div class="row small" style="margin-top: 10px">
        <button class="btn small" :disabled="busy" @click="checkNow">Check now</button>
        <span class="faint">{{ upd.checked ? `checked ${ago(upd.checked)}` : 'not checked yet' }}</span>
        <span v-if="Object.keys(upd.holds).length" class="faint">· held: {{ Object.entries(upd.holds).map(([k, v]) => `${k} ${v}`).join(', ') }}</span>
      </div>
    </div>

    <div class="card">
      <h2>Repositories</h2>
      <p class="muted small" style="margin-top: 0">A repository is a signed <code>index.json</code> on any web server. Its key signs the index and every plugin in it, so adding it trusts that key; the key is pinned, and an index or plugin signed with another key is refused. Publish your own with <code>labdex plugin repo</code>.</p>
      <ul v-if="repos.repositories.length" class="plain small repos">
        <li v-for="r in repos.repositories" :key="r.id" class="row" :class="{ faint: !r.enabled }">
          <span v-if="r.official" class="tag">official</span>
          <b>{{ r.name }}</b> <span class="mono faint">{{ r.key_id }}</span>
          <span class="mono faint ell" style="flex: 1">{{ r.url }}</span>
          <span v-if="r.error" class="c-red" :title="r.error">✕ {{ r.error }}</span>
          <span v-else-if="r.fetched_at" class="faint">checked {{ ago(r.fetched_at) }}</span>
          <button class="btn small" @click="toggleRepo(r)">{{ r.enabled ? 'Disable' : 'Enable' }}</button>
          <button class="btn small ghost" @click="removeRepo(r)">Remove</button>
        </li>
      </ul>
      <form class="row" style="flex-wrap: nowrap; margin-top: 10px" @submit.prevent="previewRepo">
        <input v-model="newRepo" type="text" inputmode="url" class="mono" placeholder="https://plugins.example.org/index.json" style="flex: 1" aria-label="Repository URL">
        <button class="btn" :disabled="busy || !newRepo.trim()">Check</button>
      </form>
      <div v-if="preview" class="stage-box">
        <div><b>{{ preview.name }}</b> <span v-if="preview.official" class="tag">official</span> · {{ preview.plugins }} plugin{{ preview.plugins === 1 ? '' : 's' }}</div>
        <div v-if="preview.description" class="small muted">{{ preview.description }}</div>
        <div class="small">Signed with key <span class="mono">{{ preview.key_id }}</span>. Compare it with the one the publisher gives you before you trust it.</div>
        <div class="row" style="margin-top: 8px"><button class="btn primary small" :disabled="busy" @click="trustRepo">Trust this repository</button>
          <button class="btn ghost small" @click="preview = null">Cancel</button></div>
      </div>
      <div v-if="repos.legacy_urls.length" class="warnbox small" style="margin-top: 10px">
        Unsigned marketplace URLs from before 0.7 aren't used any more: {{ repos.legacy_urls.join(', ') }}. Add them again here once their publisher signs them.
      </div>
      <div v-for="e in market?.errors || []" :key="e" class="err small">{{ e }}</div>
      <p v-if="repos.repositories.length" class="small muted" style="margin: 10px 0 0">Browse and install what they offer in the <RouterLink to="/plugins?tab=market">Marketplace</RouterLink>.</p>
    </div>

    <div class="card">
      <h2>Signing keys</h2>
      <p class="muted small" style="margin-top: 0"><b>Official</b> plugins are signed with the labdex project key below. <b>Community</b> plugins are signed by a repository you added, or by an author whose public key you add here. Anything else is <b>unsigned</b> and needs your explicit OK; a broken signature is never loaded.</p>
      <ul class="plain small">
        <li v-for="k in keys.official" :key="k.key_id"><span class="tag">official</span> {{ k.name }} <span class="mono faint">{{ k.key_id }}</span></li>
        <li v-for="k in keys.community" :key="k.id" class="row"><span class="tag">community</span> {{ k.name }} <span class="mono faint">{{ k.key_id }}</span>
          <span class="faint">added by {{ k.added_by }} {{ ago(k.created_at) }}</span><span class="grow"></span>
          <button class="btn icon" title="Remove key" aria-label="Remove key" @click="removeKey(k)"><Trash2 :size="15" /></button></li>
      </ul>
      <button class="btn small" style="margin-top: 10px" @click="keyOpen = true"><Plus :size="14" /> Author key</button>
      <Modal v-if="keyOpen" title="Trust an author's key" @close="keyOpen = false">
        <form id="key-form" class="kform" @submit.prevent="addKey">
          <Field label="Author" :error="kerr.name"><input v-model.trim="nk.name" placeholder="Alice" required></Field>
          <Field label="Ed25519 public key" :error="kerr.public_key" hint="PEM or base64, from labdex plugin keygen">
            <textarea v-model.trim="nk.public_key" rows="4" class="mono" required></textarea></Field>
        </form>
        <template #foot>
          <button type="button" class="btn ghost" @click="keyOpen = false">Cancel</button>
          <button form="key-form" class="btn primary">Trust this key</button>
        </template>
      </Modal>
    </div>
  </div>
</template>

<style scoped>
.iso { margin-left: 6px; font-size: 11px; color: var(--ok); border: 1px solid currentColor; border-radius: 999px; padding: 1px 8px; white-space: nowrap; }
.iso-line { margin: 0; color: var(--muted); }
.iso-line.lvl-partial, .iso-line.lvl-none { color: var(--warn); }
.iso-line.lvl-refused { color: var(--err); }
.stack > * + * { margin-top: 16px; }
.kform > * + * { margin-top: 14px; }
label.btn.disabled { opacity: .5; pointer-events: none; }
.stage-box { margin-top: 10px; padding: 10px 12px; border: 1px solid var(--accent); border-radius: 8px; display: flex; flex-direction: column; gap: 4px; }
.repos li { padding: 6px 0; border-top: 1px solid var(--line); gap: 8px; flex-wrap: nowrap; }
</style>
