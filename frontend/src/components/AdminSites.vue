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
import { Download, GitBranch, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'

const data = ref(null)
const errors = ref({})
const busy = ref(false)
const open = ref(false)
const noGit = () => ({ url: '', branch: 'main', path: '', mode: 'commit', poll: 5, token_ref: '' })
const blank = () => ({ code: '', name: '', description: '', colour: '', git: noGit(), _orig: null })
const refs = ref([]) // vault fields that may hold a git token
const form = reactive(blank())
const cleanCode = (t) => String(t || '').trim().toLowerCase().replace(/[\s_.]+/g, '-').replace(/[^a-z0-9-]/g, '').replace(/^-+|-+$/g, '')

async function load() { data.value = await api.get('/api/sites') }
onMounted(() => {
  load().catch((e) => toast(e.message, 'error'))
  api.get('/api/vault/refs').then((r) => { refs.value = r.refs }).catch(() => {})
})

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
function edit(s) {
  errors.value = {}
  const { hook_url: _h, hook_secret: _s, state: _st, ...git } = s.git || {}
  Object.assign(form, { ...blank(), ...s, git: { ...noGit(), ...git }, _orig: s.code })
  open.value = true
}
function add() { errors.value = {}; Object.assign(form, blank()); open.value = true }
async function save() {
  const { _orig, usage: _usage, ...rest } = form
  const body = { ...rest, code: cleanCode(rest.code), git: rest.git.url ? { ...rest.git, poll: Number(rest.git.poll) || 0 } : {} }
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
const syncing = ref('')
async function sync(s, prefer) {
  syncing.value = s.code
  try {
    const r = await api.post(`/api/sites/${enc(s.code)}/git/sync`, prefer ? { prefer } : {})
    data.value = { ...data.value, sites: r.sites }
    toast({ pulled: `Imported ${s.code} from git`, pushed: `Committed ${s.code} to git`, none: `${s.code} is in step with git` }[r.action] || r.action)
    await Promise.all([changed(), loadMeta()])
  } catch (e) {
    if (e.body?.sites) data.value = { ...data.value, sites: e.body.sites }
    toast(e.message, 'error')
  } finally { syncing.value = '' }
}
async function resolve(s, prefer) {
  const body = prefer === 'git'
    ? 'Imports the file from git; the edits made here since the last sync are dropped.'
    : 'Commits this site over the file in git; the changes made in git since the last sync are replaced.'
  if (await confirm({ title: prefer === 'git' ? `Keep git's ${s.code}?` : `Keep labdex's ${s.code}?`, body, action: 'Keep it', danger: true })) await sync(s, prefer)
}
const short = (sha) => (sha || '').slice(0, 10)
const hookUrl = (s) => `${location.origin}${s.git.hook_url}`
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
          <div v-if="s.git" class="li-sub git">
            <GitBranch :size="13" /> <span class="mono">{{ s.git.url }}</span> · {{ s.git.branch }} · <span class="mono">{{ s.git.path }}</span>
            <span v-if="s.git.state?.commit" class="faint"> at {{ short(s.git.state.commit) }}</span>
            <a v-if="s.git.state?.mr_url" :href="s.git.state.mr_url" target="_blank" rel="noopener"> merge request</a>
            <div v-if="s.git.state?.error" class="err small">{{ s.git.state.error }}
              <template v-if="s.git.state.conflict && data.can_edit">
                <button class="btn small" @click="resolve(s, 'git')">Keep git</button>
                <button class="btn small" @click="resolve(s, 'labdex')">Keep labdex</button></template></div>
          </div>
        </div>
        <div class="meta small">
          <RouterLink :to="{ path: '/hosts', query: { site: s.code } }">{{ plural(s.usage.hosts, 'host') }}</RouterLink>
          <RouterLink :to="{ path: '/networks', query: { site: s.code } }">{{ plural(s.usage.networks, 'network') }}</RouterLink>
          <span>{{ plural(s.usage.integrations, 'integration') }}</span>
        </div>
        <div v-if="data.can_edit" class="li-actions">
          <button class="btn icon" title="Move up" aria-label="Move up" :disabled="i === 0" @click="move(i, -1)">↑</button>
          <button v-if="s.git" class="btn icon" title="Sync with git now" aria-label="Sync with git now" :disabled="syncing === s.code" @click="sync(s)"><RefreshCw :size="15" /></button>
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
        <h4 class="sub">Git source</h4>
        <p class="muted small">The site's file lives in a repository: labdex imports new commits and commits edits made here.</p>
        <Field label="Repository" :error="errors['git.url']" hint="https://… (empty: no git source)">
          <input v-model.trim="form.git.url" class="mono" placeholder="https://gitlab.example.com/infra/lab.git"></Field>
        <template v-if="form.git.url">
          <div class="grid2">
            <Field label="Branch" :error="errors['git.branch']"><input v-model.trim="form.git.branch" class="mono" placeholder="main"></Field>
            <Field label="File" :error="errors['git.path']"><input v-model.trim="form.git.path" class="mono" :placeholder="`labdex/${cleanCode(form.code) || 'site'}.yaml`"></Field>
          </div>
          <div class="grid2">
            <Field label="Edits made here" :error="errors['git.mode']">
              <select v-model="form.git.mode"><option value="commit">Commit to the branch</option><option value="mr">Open a merge request</option></select></Field>
            <Field label="Check every (minutes)" :error="errors['git.poll']" hint="0: only on a webhook"><input v-model="form.git.poll" type="number" min="0" max="1440"></Field>
          </div>
          <Field label="Token" :error="errors['git.token_ref']" hint="A vault field with an access token (read and write; api for merge requests)">
            <input v-model.trim="form.git.token_ref" class="mono" list="git-token-refs" placeholder="vault:git/lab#token">
            <datalist id="git-token-refs"><option v-for="r in refs" :key="r.ref" :value="r.ref">{{ r.description }}</option></datalist></Field>
          <p v-if="form._orig && data.sites.find((x) => x.code === form._orig)?.git" class="muted small">
            Webhook: <code>{{ hookUrl(data.sites.find((x) => x.code === form._orig)) }}</code> with the secret
            <code>{{ data.sites.find((x) => x.code === form._orig).git.hook_secret }}</code> (GitLab: secret token; GitHub: secret, JSON).</p>
        </template>
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
.git { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.git .err { flex-basis: 100%; color: var(--danger, #c33); }
.sub { margin: 18px 0 0; font-size: 1em; }
.swatch { width: 12px; height: 12px; border-radius: var(--r-sm); flex: none; margin-right: 10px; }
.meta { display: flex; gap: 16px; color: var(--muted); white-space: nowrap; }
.fstack > * + * { margin-top: 14px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 700px) { .grid2 { grid-template-columns: minmax(0, 1fr); } .meta { display: none; } }
</style>
