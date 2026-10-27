<script setup>
// Everything that can be installed: what ships with labdex (installs instantly, nothing is downloaded)
// and what the trusted repositories offer (downloaded, signature checked, then confirmed).
import { Check, Download, PackageOpen, RefreshCw } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
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

async function load(offline = false) {
  const d = await api.get(`/api/marketplace${offline ? '?offline=1' : ''}`)
  // the offline answer comes first; never let it replace the full one
  if (offline && data.value && !data.value.offline) return
  data.value = { ...d, offline }
  emit('updates', d.plugins.filter((p) => p.update).length)
  if (open.value) open.value = d.plugins.find((p) => p.id === open.value.id) || null
}
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
const from = (p) => (p.bundled ? 'Ships with labdex' : p.repository || p.source)

async function install(p) {
  busy.value = p.id
  try {
    if (p.bundled && !p.installed) {
      await api.post('/api/marketplace/install', { id: p.id })
      kept.value = new Set([...kept.value, p.id])
      toast(`Installed ${p.name}`)
      await changed()
    } else {
      staged.value = await api.post('/api/marketplace/install', { repository_id: p.repository_id, id: p.id })
      open.value = null
    }
  } catch (e) { toast(e.message, 'error') } finally { busy.value = '' }
}
async function uninstall(p) {
  const body = p.bundled
    ? 'It stays part of labdex and you can install it again any time. Nothing else changes.'
    : 'Its files are deleted. Install it again from its repository or upload it.'
  if (!(await confirm({ title: `Uninstall ${p.name}?`, body, action: 'Uninstall', danger: true }))) return
  busy.value = p.id
  try {
    await api.del(`/api/plugins/${p.id}`)
    toast(`Uninstalled ${p.name}`)
    await changed()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = '' }
}
async function changed() {
  staged.value = null
  await Promise.all([load(data.value?.offline), loadNav()])
  emit('changed')
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
          <span v-if="open.bundled" class="faint"> · nothing to download, installs at once</span></dd>
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
.emptyp { display: flex; flex-direction: column; align-items: center; gap: 4px; color: var(--faint); padding: 32px 0; }
.emptyp p { margin: 0; }
</style>
