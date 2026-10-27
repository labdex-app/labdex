<script setup>
// Everything that can be installed: what the trusted repositories offer (downloaded, signature checked,
// then confirmed), and development checkouts (LABDEX_DEV_PLUGINS: nothing to download).
import { Check, Download, History, PackageOpen, RefreshCw, Undo2 } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { confirm } from '../confirm'
import { loadNav, toast } from '../store'
import Modal from './Modal.vue'
import PluginInstallConfirm from './PluginInstallConfirm.vue'

const emit = defineEmits(['changed', 'updates'])
const data = ref(null)
const fetching = ref(false)
const q = ref('')
const show = ref('all')
const tag = ref('')
const open = ref(null)
const staged = ref(null)
const isolation = ref(null)
const busy = ref('')
// what you just installed stays where it was until you change the filter, so cards don't jump away
const kept = ref(new Set())
watch(show, () => { kept.value = new Set() })

// Answers can arrive out of order: reading the repositories is slow, the offline answer fast.
// Each local change (install, uninstall, hold) starts a new generation; an answer asked for
// before it is stale. An offline answer keeps what the repositories said (versions, updates).
let gen = 0
let shownGen = -1
const REPO_FIELDS = ['repository', 'repository_id', 'repo_version', 'latest_version', 'versions', 'source_url', 'official_signed', 'supported', 'requires', 'compatible', 'incompatible_reason']
async function load(offline = false) {
  const asked = gen
  const d = await api.get(`/api/marketplace${offline ? '?offline=1' : ''}`)
  if (asked < shownGen) return // something changed after this was asked
  if (offline && data.value && !data.value.offline) {
    if (asked === shownGen) return // the full answer for the same state is better
    const full = Object.fromEntries(data.value.plugins.map((p) => [p.id, p]))
    for (const p of d.plugins) {
      const f = full[p.id]
      if (!f?.repository_id) continue
      for (const k of REPO_FIELDS) p[k] = f[k]
      p.update = !!(p.installed && f.update && !p.held)
    }
    d.plugins.push(...data.value.plugins.filter((p) => !d.plugins.some((x) => x.id === p.id))) // offered only by a repository
    d.errors = data.value.errors
    offline = false
  }
  shownGen = asked
  data.value = { ...d, offline }
  emit('updates', d.plugins.filter((p) => p.update).length)
  if (open.value) open.value = d.plugins.find((p) => p.id === open.value.id) || null
  else if (route.query.plugin && !opened) { opened = true; open.value = d.plugins.find((p) => p.id === route.query.plugin) || null } // a link from a notification
}
const route = useRoute()
let opened = false
async function refresh() {
  fetching.value = true
  try { await load() } catch (e) { toast(e.message, 'error') } finally { fetching.value = false }
}
onMounted(async () => {
  try { await load(true) } catch (e) { toast(e.message, 'error') }
  refresh()
  api.get('/api/plugins').then((p) => { isolation.value = p.isolation || null }).catch(() => {})
})

const all = computed(() => data.value?.plugins || [])
const tags = computed(() => {
  const n = {}
  for (const p of all.value) for (const t of p.tags || []) n[t] = (n[t] || 0) + 1
  return Object.entries(n).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 12).map(([t]) => t)
})
const counts = computed(() => ({
  all: all.value.length,
  available: all.value.filter((p) => !p.installed).length,
  installed: all.value.filter((p) => p.installed).length,
  updates: all.value.filter((p) => p.update).length,
}))
const shown = computed(() => {
  const n = q.value.trim().toLowerCase()
  return all.value.filter((p) => (show.value === 'all' || (show.value === 'available' ? !p.installed || kept.value.has(p.id) : show.value === 'installed' ? p.installed : p.update))
    && (!tag.value || (p.tags || []).includes(tag.value))
    && (!n || [p.id, p.name, p.description, p.author, ...(p.tags || []), ...(p.capabilities || [])].join(' ').toLowerCase().includes(n)))
})
const canInstall = computed(() => !!data.value?.can_install)
const capLabel = (c) => data.value?.capability_labels?.[c] || c
const from = (p) => (p.bundled ? 'Development checkout' : p.repository || p.source)

async function install(p, version = null) {
  busy.value = p.id
  try {
    if (version) {
      staged.value = await api.post('/api/marketplace/install', { repository_id: p.repository_id, id: p.id, version })
      open.value = null
    } else if (p.bundled && !p.installed) {
      await api.post('/api/marketplace/install', { id: p.id })
      kept.value = new Set([...kept.value, p.id])
      mark(p.id, { installed: true, installed_version: p.version }) // right away: reading the repositories again can take a while
      toast(`Installed ${p.name}`)
      changed()
    } else {
      staged.value = await api.post('/api/marketplace/install', { repository_id: p.repository_id, id: p.id })
      open.value = null
    }
  } catch (e) { toast(e.message, 'error') } finally { busy.value = '' }
}
async function uninstall(p) {
  const body = p.bundled
    ? 'The checkout stays on disk and you can install it again any time. Nothing else changes.'
    : 'Its files are deleted. Install it again from its repository or upload it.'
  if (!(await confirm({ title: `Uninstall ${p.name}?`, body, action: 'Uninstall', danger: true }))) return
  busy.value = p.id
  try {
    await api.del(`/api/plugins/${p.id}`)
    mark(p.id, { installed: false, installed_version: null, update: false, previous: null })
    toast(`Uninstalled ${p.name}`)
    changed()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = '' }
}
async function rollback(p) {
  if (!(await confirm({ title: `Switch ${p.name} back to ${p.previous}?`, body: `${p.installed_version} is kept, so you can switch again the same way. Integrations keep running.`, action: `Switch to ${p.previous}` }))) return
  busy.value = p.id
  try {
    const r = await api.post(`/api/plugins/${p.id}/rollback`)
    toast(`${p.name} is at ${r.version} again`)
    await changed()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = '' }
}
async function setHold(p, on) {
  busy.value = p.id
  try {
    await api.put(`/api/plugins/${p.id}/hold`, { version: on ? p.installed_version : null })
    toast(on ? `${p.name} stays at ${p.installed_version}` : `${p.name} gets updates again`)
    gen++
    await load(true)
    emit('changed')
  } catch (e) { toast(e.message, 'error') } finally { busy.value = '' }
}
const others = (p) => (p.versions || []).filter((v) => v.version !== p.installed_version)
// the notes of the versions newer than the installed one (the list is newest first), leaving out withdrawn ones
function newer(p) {
  const vs = p.versions || []
  if (!vs.length) return p.changes ? [{ version: p.installed_version || p.version, changes: p.changes, only: true }] : []
  const i = p.installed ? vs.findIndex((v) => v.version === p.installed_version) : 1
  return vs.slice(0, i < 0 ? vs.length : i).filter((v) => !v.yanked && v.changes)
}
function mark(id, fields) {
  for (const e of [data.value?.plugins?.find((x) => x.id === id), open.value?.id === id ? open.value : null]) if (e) Object.assign(e, fields)
}
async function changed() {
  staged.value = null
  gen++
  emit('changed') // the Installed tab reloads whatever happens here
  await Promise.allSettled([load(true), loadNav()]) // fast; the repositories are read again after
  load().catch(() => {})
}
</script>

<template>
  <div>
    <PluginInstallConfirm v-if="staged" :staged="staged" :isolation="isolation" style="margin-bottom: 16px" @cancel="staged = null" @done="changed" />

    <div class="filters mtools">
      <div class="seg" role="radiogroup" aria-label="Show">
        <button v-for="[k, l] in [['all', 'All'], ['available', 'Not installed'], ['installed', 'Installed'], ['updates', 'Updates']]" :key="k"
                type="button" role="radio" :aria-checked="show === k" :class="{ on: show === k }" :disabled="k === 'updates' && !counts.updates && show !== k"
                @click="show = k">{{ l }}<span class="n">{{ counts[k] }}</span></button>
      </div>
      <span class="grow"></span>
      <button class="btn icon" :disabled="fetching" title="Check the repositories again" aria-label="Check the repositories again" @click="refresh">
        <RefreshCw :size="15" :class="{ spin: fetching }" /></button>
      <input v-model="q" type="search" placeholder="Search the Marketplace" style="max-width: 260px" aria-label="Search the Marketplace">
    </div>
    <div v-if="tags.length" class="filters tagrow" role="group" aria-label="Category">
      <button type="button" class="chip" :class="{ on: !tag }" :aria-pressed="!tag" @click="tag = ''">Everything</button>
      <button v-for="t in tags" :key="t" type="button" class="chip" :class="{ on: tag === t }" :aria-pressed="tag === t" @click="tag = tag === t ? '' : t">{{ t }}</button>
    </div>
    <div v-for="e in data?.errors || []" :key="e" class="warnbox small" style="margin-bottom: 10px">Couldn't read a repository. {{ e }}</div>

    <div class="mgrid">
      <article v-for="p in shown" :key="p.id" class="card mcard" :class="{ inst: p.installed }">
        <header>
          <button type="button" class="mname" @click="open = p">{{ p.name }}</button>
          <span class="mfrom small">{{ from(p) }}<span v-if="p.official" class="off" title="Signed by the labdex project, or part of it">official</span></span>
        </header>
        <p class="small muted desc">{{ p.description }}</p>
        <div v-if="p.withdrawn || p.held" class="flags">
          <span v-if="p.withdrawn" class="flag bad" :title="p.withdrawn">{{ p.installed_version }} withdrawn</span>
          <span v-if="p.held" class="flag" title="Stays on this version: no update is offered or installed">held at {{ p.held }}</span>
        </div>
        <div class="caps"><span v-for="c in p.capabilities" :key="c" class="tag" :title="capLabel(c)">{{ c }}</span></div>
        <footer>
          <span class="mono small faint">{{ p.update ? `${p.installed_version} → ${p.repo_version}` : p.version }}</span>
          <span class="grow"></span>
          <span v-if="p.compatible === false" class="small c-red" :title="p.incompatible_reason">needs labdex {{ p.requires }}</span>
          <template v-else-if="canInstall">
            <button v-if="p.update" class="btn small primary" :disabled="!!busy" @click="install(p)"><Download :size="14" /> Update</button>
            <span v-else-if="p.installed" class="done small"><Check :size="14" /> Installed</span>
            <button v-else class="btn small" :disabled="!!busy" @click="install(p)"><Download :size="14" /> Install</button>
          </template>
          <span v-else-if="p.installed" class="done small"><Check :size="14" /> Installed</span>
          <button type="button" class="btn small ghost" @click="open = p">Details</button>
        </footer>
      </article>
    </div>
    <div v-if="data && !shown.length" class="emptyp">
      <PackageOpen :size="22" />
      <p v-if="show === 'updates'">Everything installed is up to date.</p>
      <p v-else-if="show === 'available' && !q && !tag">Every plugin is installed.</p>
      <p v-else>Nothing matches. <a href="#" @click.prevent="q = ''; tag = ''; show = 'all'">Clear the filters</a></p>
    </div>
    <p v-if="data && !canInstall" class="small faint" style="margin-top: 14px">An administrator installs and uninstalls plugins.</p>

    <Modal v-if="open" :title="open.name" wide @close="open = null">
      <p style="margin-top: 0">{{ open.description }}</p>
      <dl class="kv small">
        <dt>From</dt><dd>{{ from(open) }}<span v-if="open.official" class="off">official</span>
          <span v-if="open.bundled" class="faint"> · nothing to download, installs at once</span>
          <template v-if="open.source_url"> · <a :href="open.source_url" target="_blank" rel="noopener">source code</a></template></dd>
        <dt>Version</dt><dd class="mono">{{ open.installed_version || open.version }}<span v-if="open.update" class="faint"> · {{ open.repo_version }} available</span>
          <span v-else-if="open.repo_version && open.repo_version !== (open.installed_version || open.version)" class="faint"> · {{ open.repository }} has {{ open.repo_version }}</span></dd>
        <dt v-if="open.author">Author</dt><dd v-if="open.author">{{ open.author }}</dd>
        <dt v-if="open.requires">Needs</dt><dd v-if="open.requires" :class="{ 'c-red': open.compatible === false }">labdex {{ open.requires }}<span v-if="open.incompatible_reason"> · {{ open.incompatible_reason }}</span></dd>
        <dt>Brings</dt><dd><ul class="plain">
          <li v-for="c in open.capabilities" :key="c"><span class="tag">{{ c }}</span> {{ capLabel(c) }}</li>
          <li v-if="open.per_host">Settings per host (a Modules entry on each host)</li></ul></dd>
        <dt v-if="open.settings.length">Settings</dt><dd v-if="open.settings.length">{{ open.settings.join(', ') }}</dd>
        <dt v-if="open.actions.length">Actions</dt><dd v-if="open.actions.length">{{ open.actions.join(', ') }}<span v-if="open.schedules" class="faint">
          · {{ open.schedules }} can run on a schedule</span></dd>
        <dt v-if="open.pages.length">Pages</dt><dd v-if="open.pages.length">{{ open.pages.join(', ') }}</dd>
        <dt v-if="open.widgets">Widgets</dt><dd v-if="open.widgets">{{ open.widgets }}</dd>
        <dt v-if="open.tags.length">Tags</dt><dd v-if="open.tags.length"><span v-for="t in open.tags" :key="t" class="tag" style="margin-right: 4px">{{ t }}</span></dd>
      </dl>
      <div v-if="open.withdrawn" class="errbox small" style="margin-top: 12px">The publisher withdrew {{ open.installed_version }}: {{ open.withdrawn }}.
        <template v-if="open.update"> Update to {{ open.repo_version }}</template><template v-else-if="open.previous"> Switch back to {{ open.previous }}</template><template v-else> Install another version below</template>.</div>
      <template v-if="newer(open).length">
        <h3 class="sub3">{{ newer(open)[0].only ? 'What changed in this version' : open.installed ? `New since ${open.installed_version}` : 'What\'s new' }}</h3>
        <div class="changes small"><div v-for="v in newer(open)" :key="v.version"><b v-if="v.version" class="mono">{{ v.version }}</b><div class="pre">{{ v.changes }}</div></div></div>
      </template>
      <template v-if="open.installed && canInstall && (open.repository || open.previous)">
        <h3 class="sub3">Updates</h3>
        <label class="check"><input type="checkbox" :checked="!!open.held" :disabled="!!busy" @change="setHold(open, $event.target.checked)">
          Stay on {{ open.held || open.installed_version }}<span class="faint small">&nbsp;· no update is offered or installed</span></label>
        <div v-if="open.previous" class="row" style="margin-top: 8px">
          <button class="btn small" :disabled="!!busy" @click="rollback(open)"><Undo2 :size="14" /> Switch back to {{ open.previous }}</button>
          <span class="small faint">the version you had before, kept here</span></div>
      </template>
      <details v-if="others(open).length && open.repository" class="vers">
        <summary class="small"><History :size="14" /> {{ open.versions.length }} version{{ open.versions.length === 1 ? '' : 's' }} in {{ open.repository }}</summary>
        <div class="table-wrap"><table class="small">
          <thead><tr><th>Version</th><th>Released</th><th>Notes</th><th></th></tr></thead>
          <tbody>
            <tr v-for="v in open.versions" :key="v.version" :class="{ faint: v.yanked }">
              <td class="mono">{{ v.version }}<span v-if="v.version === open.installed_version" class="tag" style="margin-left: 6px">installed</span></td>
              <td class="nowrap">{{ v.date || '—' }}</td>
              <td class="pre"><span v-if="v.yanked" class="c-red">Withdrawn: {{ v.yanked }}.<br></span>{{ v.changes || '—' }}
                <span v-if="!v.compatible" class="c-red"> Needs labdex {{ v.labdex }}.</span></td>
              <td class="right"><button v-if="canInstall && v.version !== open.installed_version && v.compatible && !v.yanked" class="btn small ghost" :disabled="!!busy"
                @click="install(open, v.version)">{{ open.installed ? 'Switch to' : 'Install' }} {{ v.version }}</button></td>
            </tr>
          </tbody>
        </table></div>
      </details>
      <p v-if="!open.bundled && !open.installed && open.repository" class="small muted">Downloaded from {{ open.repository }}. labdex checks its signature against the repository's key and shows you what it runs before anything is installed.</p>
      <template #foot>
        <RouterLink v-if="open.installed" :to="`/plugins/${open.id}`" class="btn ghost" @click="open = null">Open plugin page</RouterLink>
        <span class="grow"></span>
        <template v-if="canInstall && open.compatible !== false">
          <button v-if="open.installed && (open.bundled || open.repository)" class="btn danger" :disabled="!!busy" @click="uninstall(open)">Uninstall</button>
          <button v-if="open.update" class="btn primary" :disabled="!!busy" @click="install(open)">Update to {{ open.repo_version }}</button>
          <button v-else-if="!open.installed" class="btn primary" :disabled="!!busy" @click="install(open)">Install</button>
        </template>
        <button v-else type="button" class="btn ghost" @click="open = null">Close</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.mtools { margin-bottom: 8px; }
.tagrow { margin-bottom: 16px; }
@media (max-width: 640px) {
  .mtools .seg { width: 100%; }
  .mtools .seg button { flex: 1; padding: 5px 6px; white-space: nowrap; }
  .mtools input[type=search] { max-width: none !important; flex: 1; }
}
.seg .n { margin-left: 6px; font-size: 11.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.mgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.mcard { margin: 0; display: flex; flex-direction: column; gap: 8px; }
.mcard.inst { background: transparent; }
.mcard header { display: flex; flex-direction: column; gap: 1px; }
.mname { all: unset; cursor: pointer; font-weight: 650; font-size: 15px; color: var(--ink); }
.mname:hover { text-decoration: underline; }
.mname:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 3px; }
.mfrom { color: var(--faint); }
.off { margin-left: 6px; font-size: 11px; font-weight: 700; color: var(--accent-soft); }
.desc { margin: 0; display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.caps { display: flex; flex-wrap: wrap; gap: 4px; }
.mcard footer { display: flex; align-items: center; gap: 6px; margin-top: auto; padding-top: 8px; border-top: 1px solid var(--line); }
.done { display: inline-flex; align-items: center; gap: 4px; color: var(--ok); }
.kv ul { margin: 0; display: flex; flex-direction: column; gap: 4px; }
.kv ul.plain li { padding: 0; line-height: 1.6; word-break: normal; }
.kv ul .tag { margin-right: 6px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
.flags { display: flex; gap: 6px; flex-wrap: wrap; }
.flag { font-size: 11.5px; font-weight: 600; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line-strong); color: var(--muted); }
.flag.bad { color: var(--err); border-color: currentColor; }
.sub3 { font-size: 13px; margin: 18px 0 6px; }
.changes { background: var(--raise); border-radius: var(--r-ctl); padding: 8px 10px; max-height: 220px; overflow: auto; }
.vers { margin-top: 16px; }
.pre { white-space: pre-line; }
.changes > div + div { margin-top: 8px; }
.vers summary { cursor: pointer; display: inline-flex; align-items: center; gap: 6px; color: var(--muted); }
.vers table td { vertical-align: top; }
.emptyp { display: flex; flex-direction: column; align-items: center; gap: 4px; color: var(--faint); padding: 32px 0; }
.emptyp p { margin: 0; }
</style>
