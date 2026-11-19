<script setup>
// Every host agent at a glance: linked or not, what it may do, its certificate, its latest metrics.
// One host's agent, its jobs and its discovery are on the host's Agent tab (HostAgent.vue).
import { computed, ref } from 'vue'
import { useQuery } from '../query'
import { ago, stamp, toast } from '../store'

const query = useQuery(() => '/api/agents', { onError: (e) => toast(e.message, 'error') })
const agents = computed(() => query.data.value?.agents || [])
const q = ref('')
const filter = ref('')

const writes = (a) => (a.capabilities || []).filter((c) => (a.host_allow || []).includes(c))
const certState = (a) => {
  const c = a.cert || {}
  if (!c.mode || c.mode === 'off') return ''
  if (!c.issued) return 'none'
  if (c.not_after && new Date(c.not_after) - Date.now() < 14 * 86400e3) return 'expiring'
  return a.linked ? (c.on_link ? 'used' : 'unused') : 'issued'
}
const pct = (used, total) => (total ? Math.round((100 * used) / total) : null)

const TILES = [
  ['linked', 'linked', (a) => a.linked],
  ['offline', 'not linked', (a) => !a.linked],
  ['writes', 'writes on', (a) => writes(a).length > 0],
  ['root', 'run as root', (a) => a.root],
  ['nocert', 'link without a certificate', (a) => a.linked && ['unused', 'none'].includes(certState(a))],
]
const counts = computed(() => Object.fromEntries(TILES.map(([k, , f]) => [k, agents.value.filter(f).length])))
const rows = computed(() => {
  const n = q.value.trim().toLowerCase()
  const f = TILES.find(([k]) => k === filter.value)?.[2]
  return agents.value
    .filter((a) => (!f || f(a)) && (!n || [a.host, a.hostname, a.version, a.facts?.os].join(' ').toLowerCase().includes(n)))
    .sort((a, b) => Number(a.linked) - Number(b.linked) || a.host.localeCompare(b.host))
})
const mode = computed(() => agents.value[0]?.cert?.mode || '')
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Agents</h1>
      <span class="sub">{{ counts.linked }} of {{ agents.length }} linked</span>
      <span class="grow"></span>
      <input v-model="q" type="search" placeholder="Search hosts, OS, version" style="max-width: 300px" aria-label="Search agents">
    </div>

    <div class="tiles">
      <button v-for="[k, label] in TILES" :key="k" class="tile" :class="[k, { on: filter === k }]" @click="filter = filter === k ? '' : k">
        <b>{{ counts[k] }}</b><span>{{ label }}</span>
      </button>
    </div>

    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Host</th><th>Link</th><th>Runs as</th><th>Writes</th><th>Certificate</th><th class="num">CPU</th><th class="num">Memory</th><th class="num">/</th><th>Version</th></tr></thead>
          <tbody>
            <tr v-for="a in rows" :key="a.id">
              <td><RouterLink :to="{ path: `/hosts/${a.host}`, query: { tab: 'agent' } }" class="mono">{{ a.host }}</RouterLink>
                <div v-if="a.facts?.os" class="small faint">{{ a.facts.os }}</div></td>
              <td>
                <span class="st" :class="a.linked ? 'ok' : 'bad'">{{ a.linked ? 'linked' : 'not linked' }}</span>
                <div class="small faint" :title="stamp(a.last_seen)">{{ a.last_seen ? `seen ${ago(a.last_seen)}` : 'never seen' }}</div>
              </td>
              <td><span :class="{ warn: a.root }">{{ a.root ? 'root' : 'labdex-agent' }}</span></td>
              <td><span v-if="writes(a).length" class="mono small">{{ writes(a).join(', ') }}</span><span v-else class="faint small">read-only</span></td>
              <td class="small" :title="a.cert?.sha256 || ''">
                <span v-if="certState(a) === 'used'" class="ok">used</span>
                <span v-else-if="certState(a) === 'unused'" class="warn">not on the link</span>
                <span v-else-if="certState(a) === 'none'" class="warn">none</span>
                <span v-else-if="certState(a) === 'expiring'" class="warn">runs out soon</span>
                <span v-else-if="certState(a) === 'issued'" class="faint">issued</span>
                <span v-else class="faint">—</span>
                <div v-if="a.cert?.not_after" class="faint">until {{ new Date(a.cert.not_after).toLocaleDateString() }}</div>
              </td>
              <td class="num">{{ a.linked && a.metrics?.cpu_percent != null ? `${Math.round(a.metrics.cpu_percent)} %` : '—' }}</td>
              <td class="num">{{ a.linked && pct(a.metrics?.mem_total - a.metrics?.mem_available, a.metrics?.mem_total) != null ? `${pct(a.metrics.mem_total - a.metrics.mem_available, a.metrics.mem_total)} %` : '—' }}</td>
              <td class="num">{{ a.linked && pct(a.metrics?.root_used, a.metrics?.root_total) != null ? `${pct(a.metrics.root_used, a.metrics.root_total)} %` : '—' }}</td>
              <td class="small mono">{{ a.version || '—' }}</td>
            </tr>
            <tr v-if="!rows.length"><td colspan="9" class="empty">{{ agents.length ? 'Nothing matches.' : 'No agents yet.' }}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <p class="hint">
      Enrol an agent on a host's <b>Agent</b> tab. Client certificates: <span class="mono">{{ mode || '—' }}</span>
      (<a href="https://labdex-app.github.io/labdex/guide/agent/#client-certificates" target="_blank" rel="noopener">LABDEX_AGENT_MTLS</a>).
      Metrics for VictoriaMetrics: <span class="mono">/api/agents/metrics</span>.
    </p>
  </div>
</template>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px; }
.tile { font: inherit; text-align: left; background: var(--card); border: 1px solid var(--line); border-radius: var(--r-box); padding: 10px 14px;
  display: flex; flex-direction: column; gap: 2px; cursor: pointer; color: var(--muted); }
.tile b { font-size: calc(22px * var(--text)); color: var(--ink); }
.tile.linked b { color: var(--ok); }
.tile.offline b, .tile.nocert b { color: var(--warn); }
.tile.on { border-color: var(--accent); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.st { font-size: 12px; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.ok { color: var(--ok); }
.bad { color: var(--err); }
.warn { color: var(--warn); }
</style>
