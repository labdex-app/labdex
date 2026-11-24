<script setup>
// Every host agent at a glance: linked or not, what it may do, its certificate, its latest metrics.
// One host's agent, its jobs and its discovery are on the host's Agent tab (HostAgent.vue).
import { computed, ref } from 'vue'
import { Antenna } from 'lucide-vue-next'
import { useQuery } from '../query'
import { ago, stamp, toast } from '../store'
import Pager from '../components/Pager.vue'
import { usePaged } from '../paged'

const query = useQuery(() => '/api/agents', { onError: (e) => toast(e.message, 'error') })
// hosts with no agent yet: the next ones to enrol
const hostsQ = useQuery(() => '/api/hosts?fields=name,status,kind', { onError: () => {} })
const without = computed(() => {
  const have = new Set(agents.value.map((a) => a.host))
  const all = hostsQ.data.value?.hosts || hostsQ.data.value || []
  return (Array.isArray(all) ? all : []).filter((h) => !have.has(h.name) && h.status !== 'offline' && h.kind !== 'vip')
})
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

// the cards: key, label, test, the colour of the number, the line under it
const TILES = [
  ['linked', 'Linked', (a) => a.linked, 'ok', 'connected now'],
  ['offline', 'Not linked', (a) => !a.linked, 'warn', 'enrolled, no connection'],
  ['writes', 'Writes on', (a) => writes(a).length > 0, '', 'may change their host'],
  ['root', 'Run as root', (a) => a.root, '', 'not as labdex-agent'],
  ['nocert', 'No certificate', (a) => a.linked && ['unused', 'none'].includes(certState(a)), 'warn', 'linked without one'],
]
const BAR = { ok: 'var(--ok)', warn: 'var(--warn)', '': 'var(--accent)' }
const counts = computed(() => Object.fromEntries(TILES.map(([k, , f]) => [k, agents.value.filter(f).length])))
const rows = computed(() => {
  const n = q.value.trim().toLowerCase()
  const f = TILES.find(([k]) => k === filter.value)?.[2]
  return agents.value
    .filter((a) => (!f || f(a)) && (!n || [a.host, a.hostname, a.version, a.facts?.os].join(' ').toLowerCase().includes(n)))
    .sort((a, b) => Number(a.linked) - Number(b.linked) || a.host.localeCompare(b.host))
})
const { page, rows: paged } = usePaged(rows, { resetOn: [q, filter] })
const certUntil = (a) => (a.cert?.not_after ? new Date(a.cert.not_after) : null)
const certDays = (a) => (certUntil(a) ? Math.floor((certUntil(a) - Date.now()) / 86400e3) : null)
const mode = computed(() => agents.value[0]?.cert?.mode || '')
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Agents</h1>
      <span class="h-tag">{{ counts.linked }}/{{ agents.length }} linked</span>
      <span class="grow"></span>
      <input v-model="q" type="search" placeholder="Search hosts, OS, version" aria-label="Search agents">
    </div>

    <div class="kpis">
      <button v-for="[k, label, , tone, sub] in TILES" :key="k" class="kpi" :class="[k, tone && `t-${tone}`, { on: filter === k }]" :aria-pressed="filter === k" @click="filter = filter === k ? '' : k">
        <span class="label">{{ label }}</span>
        <b>{{ counts[k] }}</b>
        <div class="bar"><div :style="{ width: `${pct(counts[k], agents.length) || 0}%`, background: BAR[tone] }"></div></div>
        <span class="small muted">{{ sub }}</span>
      </button>
      <button class="kpi" :class="{ on: filter === 'none' }" :aria-pressed="filter === 'none'" @click="filter = filter === 'none' ? '' : 'none'">
        <span class="label">No agent</span>
        <b>{{ without.length }}</b>
        <div class="bar"><div :style="{ width: `${pct(without.length, without.length + agents.length) || 0}%`, background: 'var(--line-strong)' }"></div></div>
        <span class="small muted">hosts to enrol</span>
      </button>
    </div>

    <div v-if="filter === 'none'" class="card flush">
      <div class="table-wrap"><table>
        <thead><tr><th>Host</th><th class="r">Agent</th></tr></thead>
        <tbody>
          <tr v-for="h in without" :key="h.name"><td><RouterLink :to="`/hosts/${h.name}`" class="mono">{{ h.name }}</RouterLink></td>
            <td class="r"><RouterLink :to="{ path: `/hosts/${h.name}`, query: { tab: 'agent' } }" class="btn small">Enrol</RouterLink></td></tr>
          <tr v-if="!without.length"><td colspan="2" class="empty">Every host has an agent.</td></tr>
        </tbody>
      </table></div>
    </div>

    <div v-else class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Host</th><th>Link</th><th>Runs as</th><th>Writes</th><th>Certificate</th><th class="num">CPU</th><th class="num">Memory</th><th class="num">/</th><th>Version</th></tr></thead>
          <tbody>
            <tr v-for="a in paged" :key="a.id">
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
                <div v-if="certUntil(a)" :class="certDays(a) < 30 ? 'warn' : 'faint'">until {{ certUntil(a).toLocaleDateString() }}</div>
              </td>
              <td class="num mono">{{ a.linked && a.metrics?.cpu_percent != null ? `${Math.round(a.metrics.cpu_percent)} %` : '—' }}</td>
              <td class="num mono">{{ a.linked && pct(a.metrics?.mem_total - a.metrics?.mem_available, a.metrics?.mem_total) != null ? `${pct(a.metrics.mem_total - a.metrics.mem_available, a.metrics.mem_total)} %` : '—' }}</td>
              <td class="num mono">{{ a.linked && pct(a.metrics?.root_used, a.metrics?.root_total) != null ? `${pct(a.metrics.root_used, a.metrics.root_total)} %` : '—' }}</td>
              <td class="small mono">{{ a.version || '—' }}</td>
            </tr>
            <tr v-if="!rows.length"><td colspan="9" class="empty">
              <template v-if="agents.length">Nothing matches.</template>
              <template v-else>
                <div class="e-ic"><Antenna :size="18" /></div>
                <div class="e-t">No agents yet</div>
                <p>An agent keeps one outgoing connection to labdex, so a host needs no open port. Enrol one on a host's Agent tab.</p>
                <div class="e-acts"><RouterLink to="/hosts" class="btn">Pick a host</RouterLink>
                  <a href="https://labdex-app.github.io/labdex/guide/agent/" target="_blank" rel="noopener" class="btn ghost">How agents work</a></div>
              </template></td></tr>
          </tbody>
        </table>
      </div>
      <Pager v-model="page" :total="rows.length" unit="agents" />
    </div>
    <p class="hint mt-3">
      Enrol an agent on a host's <b>Agent</b> tab. Client certificates: <span class="mono">{{ mode || '—' }}</span>
      (<a href="https://labdex-app.github.io/labdex/guide/agent/#client-certificates" target="_blank" rel="noopener">LABDEX_AGENT_MTLS</a>).
      Metrics for VictoriaMetrics: <span class="mono">/api/agents/metrics</span>.
    </p>
  </div>
</template>

<style scoped>
.num { text-align: right; font-variant-numeric: tabular-nums; }
.r { text-align: right; }
.st { font-size: 12px; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.ok { color: var(--ok); }
.bad { color: var(--err); }
.warn { color: var(--warn); }
</style>
