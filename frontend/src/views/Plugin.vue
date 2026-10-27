<script setup>
// One plugin: what it brings, its instances and their recent jobs.
import { Plus } from 'lucide-vue-next'
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import JobBadge from '../components/JobBadge.vue'
import TrustBadge from '../components/TrustBadge.vue'
import { ago, loadNav, toast } from '../store'

const props = defineProps({ id: String })
const router = useRouter()
const d = ref(null)
const notFound = ref(false)
const newName = ref('')
const nameError = ref('')

async function load() {
  try {
    d.value = await api.get(`/api/plugins/${props.id}`)
    newName.value = d.value.instances.length ? `${d.value.id}-${d.value.instances.length + 1}` : d.value.id
  } catch (e) {
    if (e.status === 404) notFound.value = true
    else toast(e.message, 'error')
  }
}
onMounted(load)
watch(() => props.id, load)

async function install() {
  try {
    await api.post('/api/marketplace/install', { id: d.value.id })
    toast(`Installed ${d.value.name}`)
    await Promise.all([load(), loadNav()])
  } catch (e) { toast(e.message, 'error') }
}
async function create() {
  try {
    const r = await api.post('/api/integrations', { plugin_id: d.value.id, name: newName.value })
    router.push(`/integrations/${r.name}`)
  } catch (e) {
    nameError.value = e instanceof ApiError && e.errors.length ? e.errors.map((x) => x.message).join('; ') : e.message
  }
}
</script>

<template>
  <div class="page">
    <div v-if="notFound" class="errbox">No plugin “{{ id }}”. <RouterLink to="/plugins">Back</RouterLink></div>
    <template v-else-if="d">
      <div class="page-head">
        <RouterLink to="/plugins" class="btn ghost small">← Plugins</RouterLink>
        <h1>{{ d.name }}</h1>
        <span class="sub mono">{{ d.id }} {{ d.version }}</span>
        <TrustBadge :trust="d.trust" /><span v-if="d.isolated" class="tag" title="Its code runs in a locked-down process of its own">isolated</span>
      </div>
      <p class="muted" style="margin-top: -6px">{{ d.description }}</p>
      <div v-if="d.installed === false" class="warnbox row" style="margin-bottom: 16px">
        <span class="grow">Not installed. It ships with labdex; install it to add instances.</span>
        <button v-if="d.can_install" class="btn primary small" @click="install">Install</button>
      </div>
      <div v-if="d.error" class="errbox" style="margin-bottom: 16px">The plugin failed to load: <code>{{ d.error }}</code></div>

      <div class="split">
        <div class="stack">
          <div class="card flush">
            <div class="row" style="padding: 14px 18px 4px"><h2 style="margin: 0">Instances</h2></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Name</th><th>State</th><th>Last run</th><th>Applies to</th></tr></thead>
                <tbody>
                  <tr v-for="i in d.instances" :key="i.id" class="click" v-rowlink @click="router.push(`/integrations/${i.name}`)">
                    <td><RouterLink :to="`/integrations/${i.name}`" class="mono" @click.stop>{{ i.name }}</RouterLink></td>
                    <td><span v-if="i.enabled" class="status s-active">enabled</span><span v-else class="status s-offline">disabled</span></td>
                    <td><template v-if="i.last_job"><JobBadge :status="i.last_job.status" /> <span class="small faint">{{ i.last_job.action }} · {{ ago(i.last_job.created_at) }}</span></template>
                      <span v-else class="faint small">never</span></td>
                    <td class="small mono">{{ d.per_host ? (i.selectors.join(' ') || 'chosen hosts') : '—' }}</td>
                  </tr>
                  <tr v-if="!d.instances.length"><td colspan="4" class="empty">No instance yet.</td></tr>
                </tbody>
              </table>
            </div>
            <form v-if="d.can_add && !d.error" class="row add" @submit.prevent="create">
              <input v-model.trim="newName" type="text" class="mono" style="max-width: 240px" aria-label="Instance name">
              <button class="btn primary small"><Plus :size="14" /> Add instance</button>
              <span v-if="nameError" class="err">{{ nameError }}</span>
              <span v-else class="small faint">Each instance has its own settings, e.g. one per firewall.</span>
            </form>
          </div>

          <div v-if="d.jobs.length" class="card flush">
            <div class="row" style="padding: 14px 18px 4px"><h2 style="margin: 0">Recent jobs</h2></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>#</th><th>Instance</th><th>Action</th><th>Status</th><th>When</th></tr></thead>
                <tbody>
                  <tr v-for="j in d.jobs" :key="j.id" class="click" v-rowlink @click="router.push(`/jobs/${j.id}`)">
                    <td class="faint">{{ j.id }}</td><td class="mono small">{{ j.integration }}</td><td>{{ j.action }}</td>
                    <td><JobBadge :status="j.status" /></td><td class="small">{{ ago(j.created_at) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <aside class="stack">
          <div class="card">
            <h3 style="margin-top: 0">Brings</h3>
            <ul class="plain caps">
              <li v-for="(label, c) in d.capability_labels" :key="c"><span class="tag">{{ c }}</span> <span class="small muted">{{ label }}</span></li>
            </ul>
            <template v-if="d.actions.length">
              <h3>Actions</h3>
              <ul class="plain small">
                <li v-for="a in d.actions" :key="a.id"><b>{{ a.label || a.id }}</b> <span class="faint">{{ a.mode }}{{ a.scope === 'host' ? ', per host' : '' }}</span>
                  <div v-if="a.description" class="faint">{{ a.description }}</div></li>
              </ul>
            </template>
            <template v-if="d.hooks.length">
              <h3>Reacts to</h3>
              <ul class="plain small mono"><li v-for="h in d.hooks" :key="h.action">{{ (h.events || []).join(', ') }} → {{ h.action }}</li></ul>
            </template>
          </div>
          <div class="card">
            <h3 style="margin-top: 0">About</h3>
            <dl class="kv small">
              <dt>version</dt><dd class="mono">{{ d.version || '—' }}</dd>
              <template v-if="d.requires"><dt>works with</dt><dd class="mono">labdex {{ d.requires }}</dd></template>
              <dt>source</dt><dd>{{ d.builtin ? 'built in' : d.bundled ? 'ships with labdex; updates from the official repository' : d.replaces_bundled ? `installed update (labdex ships ${d.replaces_bundled})` : 'installed' }}{{ d.isolated ? '; runs isolated' : '' }}</dd>
              <dt>signature</dt><dd>{{ d.trust.detail || d.trust.status }}</dd>
              <dt v-if="d.author">author</dt><dd v-if="d.author">{{ d.author }}</dd>
              <dt v-if="d.homepage">homepage</dt><dd v-if="d.homepage"><a :href="d.homepage" target="_blank" rel="noopener">{{ d.homepage }}</a></dd>
              <dt v-if="d.tags.length">tags</dt><dd v-if="d.tags.length">{{ d.tags.join(', ') }}</dd>
            </dl>
          </div>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.add { padding: 10px 18px 14px; border-top: 1px solid var(--line); }
.caps li { margin-bottom: 4px; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; }
.kv dt { color: var(--faint); } .kv dd { margin: 0; overflow-wrap: anywhere; }
</style>
