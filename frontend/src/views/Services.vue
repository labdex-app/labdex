<script setup>
// Every service in the inventory, as a launcher: grouped by zone, with what Discovery saw.
import FilterSelect from '../components/FilterSelect.vue'
import { computed, ref } from 'vue'
import ZoneChip from '../components/ZoneChip.vue'
import { useQuery } from '../query'
import { toast, zoneLong } from '../store'

const query = useQuery('/api/services', { onError: (e) => toast(e.message, 'error') })
const services = computed(() => query.data.value || [])
const q = ref('')
const status = ref('')
const linkedOnly = ref(false)
let saved = 'cards'
try { saved = localStorage.getItem('labdex-services-view') || 'cards' } catch { /* */ }
const view = ref(saved)
function setView(v) { view.value = v; try { localStorage.setItem('labdex-services-view', v) } catch { /* */ } }

const STATUS = { open: ['up', 'answering'], closed: ['down', 'not answering'], unknown: ['', 'not scanned'] }
const visible = computed(() => {
  const n = q.value.trim().toLowerCase()
  return services.value.filter((s) =>
    (!status.value || s.status === status.value) && (!linkedOnly.value || s.url) &&
    (!n || [s.name, s.description, s.host, s.fun, s.function, s.url, s.zone, ...(s.addresses || [])].join(' ').toLowerCase().includes(n)))
})
const groups = computed(() => {
  const by = {}
  for (const s of visible.value) (by[s.zone] ||= []).push(s)
  return Object.entries(by).sort(([a], [b]) => a.localeCompare(b)).map(([zone, items]) => ({ zone, items }))
})
const counts = computed(() => ({
  open: services.value.filter((s) => s.status === 'open').length,
  closed: services.value.filter((s) => s.status === 'closed').length,
  unknown: services.value.filter((s) => s.status === 'unknown').length,
}))
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Services</h1>
      <span class="sub">{{ visible.length }} of {{ services.length }}</span>
      <span class="grow"></span>
      <input v-model="q" type="search" placeholder="Search services, hosts, addresses" style="max-width: 300px" aria-label="Search services">
      <div class="seg" role="group" aria-label="View">
        <button :class="{ on: view === 'cards' }" @click="setView('cards')">Cards</button>
        <button :class="{ on: view === 'table' }" @click="setView('table')">Table</button>
      </div>
    </div>

    <div class="filters">
      <FilterSelect :model-value="status" label="Status" :options="Object.entries(STATUS).map(([k, v]) => ({ value: k, label: `${v[1]} (${counts[k]})` }))" @update:model-value="(v) => (status = v || '')" />
      <label class="check small" style="margin-left: 8px"><input v-model="linkedOnly" type="checkbox"> with a link</label>
    </div>

    <template v-if="view === 'cards'">
      <section v-for="g in groups" :key="g.zone" class="grp">
        <h2 class="grp-h"><ZoneChip :zone="g.zone" /> <span class="faint small">{{ zoneLong(g.zone) }}</span></h2>
        <div class="cards">
          <div v-for="s in g.items" :key="s.host + s.name" class="svc-card">
            <div class="row" style="flex-wrap: nowrap; gap: 8px">
              <span class="dot" :class="STATUS[s.status][0]" :title="STATUS[s.status][1] + (s.status_by ? ` (${s.status_by})` : '')"></span>
              <b class="nm">{{ s.name }}</b>
              <span class="grow"></span>
              <span v-if="s.port" class="mono small faint">{{ s.port }}/{{ s.proto }}</span>
            </div>
            <div v-if="s.description" class="small desc">{{ s.description }}</div>
            <div class="small">
              <RouterLink :to="`/hosts/${s.host}?tab=services`">{{ s.fun || s.host }}</RouterLink>
              <span v-if="s.function" class="faint"> · {{ s.function }}</span>
            </div>
            <div class="row foot">
              <a v-if="s.url" :href="s.url" target="_blank" rel="noopener" class="btn small primary">Open ↗</a>
              <span v-if="s.url" class="mono small faint url" :title="s.url">{{ s.url.replace(/^https?:\/\//, '') }}</span>
              <span v-else class="mono small faint">{{ (s.addresses || []).slice(0, 2).join(' ') }}</span>
            </div>
          </div>
        </div>
      </section>
      <p v-if="!groups.length" class="empty">Nothing matches. Services are added on each host's Services tab.</p>
    </template>

    <div v-else class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th></th><th>Service</th><th>Host</th><th>Zone</th><th>Port</th><th>Listens on</th><th>Allow from</th><th>Link</th></tr></thead>
          <tbody>
            <tr v-for="s in visible" :key="s.host + s.name">
              <td><span class="dot" :class="STATUS[s.status][0]" :title="STATUS[s.status][1] + (s.status_by ? ` (${s.status_by})` : '')"></span></td>
              <td><b>{{ s.name }}</b><div v-if="s.description" class="small faint">{{ s.description }}</div></td>
              <td><RouterLink :to="`/hosts/${s.host}?tab=services`">{{ s.fun || s.host }}</RouterLink></td>
              <td><ZoneChip :zone="s.zone" /></td>
              <td class="mono small">{{ s.port ? `${s.port}/${s.proto}` : '—' }}</td>
              <td class="mono small">{{ s.bind ? (s.addresses || []).join(' ') : 'every address' }}</td>
              <td class="mono small">{{ (s.allow_from || []).join(', ') || '—' }}</td>
              <td><a v-if="s.url" :href="s.url" target="_blank" rel="noopener" class="small">{{ s.url.replace(/^https?:\/\//, '') }} ↗</a><span v-if="s.srv" class="small faint mono"> {{ s.srv }}</span></td>
            </tr>
            <tr v-if="!visible.length"><td colspan="8" class="empty">{{ services.length ? 'Nothing matches.' : 'No services yet. Add them on a host’s Services tab, or import them with the Traefik or Docker plugin.' }}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <p class="hint">Status comes from a monitor (e.g. Uptime Kuma) when one checks the service, otherwise from Discovery: answering when a scan saw the port open on an address the service listens on.</p>
  </div>
</template>

<style scoped>
.grp { margin-bottom: 22px; }
.grp-h { display: flex; align-items: center; gap: 10px; font-size: 14px; margin: 0 0 10px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; }
.svc-card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.svc-card .nm { font-size: 15px; overflow-wrap: anywhere; }
.svc-card .desc { color: var(--muted); }
.svc-card .foot { margin-top: auto; gap: 8px; flex-wrap: nowrap; min-width: 0; }
.svc-card .foot .btn { flex: none; white-space: nowrap; }
.svc-card .url { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--faint); opacity: .45; flex: none; margin-right: 4px; }
.dot.up { background: var(--ok); opacity: 1; }
.dot.down { background: var(--err); opacity: 1; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { font: inherit; font-size: 12.5px; padding: 5px 10px; background: none; border: 0; color: var(--muted); cursor: pointer; }
.seg button.on { background: var(--raise); color: var(--ink); }
</style>
