<script setup>
// The host's agent: enrol it, see whether it's linked, its metrics, what it discovered (ports,
// the processes behind them, systemd units, containers) and its jobs. Writes (updates, reboots,
// Ansible) appear only once an administrator switched them on here and the host allows them.
import { Play, RefreshCw, ShieldOff } from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, enc } from '../api'
import { confirm } from '../confirm'
import { ago, can, copy, stamp, state, toast } from '../store'
import Loading from './Loading.vue'

const props = defineProps({ host: { type: String, required: true } })
const data = ref(null)
const enrolment = ref(null) // {token, command, expires_at}
const allowRoot = ref(false)
const reboot = ref(false)
const playbook = ref('ansible/site.yml')
const checkOnly = ref(true)
const busy = ref(false)
const isAdmin = computed(() => !!state.meta?.is_admin)
const mayEdit = computed(() => can('host', 'edit'))
const agent = computed(() => data.value?.agent)
const disc = computed(() => data.value?.discovery)
const procs = computed(() => Object.fromEntries((disc.value?.processes || []).map((p) => [p.pid, p])))

async function load() {
  try { data.value = await api.get(`/api/hosts/${enc(props.host)}/agent`) } catch (x) { toast(x.message, 'error') }
}
watch(() => props.host, load, { immediate: true })
const timer = setInterval(() => { if (!document.hidden) load() }, 10000)
onBeforeUnmount(() => clearInterval(timer))

async function enrol() {
  busy.value = true
  try { enrolment.value = await api.post(`/api/hosts/${enc(props.host)}/agent/token`, { allow_root: allowRoot.value }) } catch (x) { toast(x.message, 'error') } finally { busy.value = false }
}
async function run(type, params = {}) {
  busy.value = true
  try {
    await api.post(`/api/hosts/${enc(props.host)}/agent/jobs`, { type, params })
    toast(`${type} queued`)
    await load()
  } catch (x) { toast(x.message, 'error') } finally { busy.value = false }
}
async function save(patch) {
  try {
    await api.patch(`/api/hosts/${enc(props.host)}/agent`, patch)
    await load()
  } catch (x) { toast(x.message, 'error') }
}
function toggleCap(c) {
  const caps = new Set(agent.value.capabilities)
  caps.has(c) ? caps.delete(c) : caps.add(c)
  save({ capabilities: [...caps] })
}
async function revoke() {
  if (!(await confirm({ title: `Revoke the agent on ${props.host}?`, body: 'Its key stops working at once and its link closes. To use an agent here again, enrol it anew.', action: 'Revoke', danger: true }))) return
  try { await api.del(`/api/hosts/${enc(props.host)}/agent`); enrolment.value = null; await load() } catch (x) { toast(x.message, 'error') }
}
const gb = (n) => (n ? `${(n / 1024 ** 3).toFixed(1)} GB` : '—')
const pct = (used, total) => (total ? `${Math.round((100 * used) / total)} %` : '—')
const days = (s) => (s ? `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h` : '—')
const canRun = (cap) => agent.value?.capabilities.includes(cap) && agent.value?.host_allow.includes(cap)
const statusClass = (s) => ({ succeeded: 'ok', failed: 'bad', refused: 'bad', expired: 'warn', cancelled: 'warn' }[s] || '')
</script>

<template>
  <Loading v-if="data === null" />
  <div v-else-if="!agent" class="card">
    <p class="m-0">No agent on this host. The agent is a small service that dials out to labdex (the host needs no open port), reports
      metrics, listening ports, services and updates, and runs only signed jobs from a fixed list, read-only unless you switch writes on.</p>
    <template v-if="mayEdit">
      <div class="row mt-2" style="gap: 12px">
        <button class="btn" :disabled="busy" @click="enrol">Enrol an agent</button>
        <label v-if="isAdmin" class="small"><input v-model="allowRoot" type="checkbox"> run it as root (needed for Ansible)</label>
      </div>
      <div v-if="enrolment" class="mt-2">
        <p class="small m-0">Run this on {{ host }} as root before {{ stamp(enrolment.expires_at) }}. The token works once.</p>
        <pre class="mono small cmd" role="button" tabindex="0" title="Copy" @click="copy(enrolment.command)">{{ enrolment.command }}</pre>
        <p class="small faint m-0">Add <code>--allow "patch reboot ansible"</code> for the writes this host may accept; without it the agent stays read-only.</p>
      </div>
    </template>
  </div>
  <template v-else>
    <div class="grid2">
      <div class="card">
        <div class="row" style="justify-content: space-between">
          <strong>Agent <span class="mono small faint">{{ agent.id }}</span></strong>
          <span :class="['st', agent.linked ? 'ok' : 'warn']">{{ agent.linked ? 'linked' : 'not linked' }}</span>
        </div>
        <dl class="kv small mt-2">
          <dt>last seen</dt><dd :title="stamp(agent.last_seen)">{{ ago(agent.last_seen) }}<span v-if="agent.address" class="faint"> from {{ agent.address }}</span></dd>
          <dt>runs as</dt><dd>{{ agent.root ? 'root' : agent.facts.user || 'labdex-agent' }}<span v-if="agent.allow_root" class="faint"> (root allowed)</span></dd>
          <dt>version</dt><dd>{{ agent.version || '—' }} <span class="faint">on {{ agent.hostname }}</span></dd>
          <template v-if="agent.facts.os"><dt>os</dt><dd>{{ agent.facts.os }} · {{ agent.facts.kernel }}</dd></template>
          <template v-if="agent.cert && agent.cert.mode !== 'off'">
            <dt>certificate</dt>
            <dd v-if="agent.cert.issued" :title="agent.cert.sha256">until {{ stamp(agent.cert.not_after) }}
              <span class="faint">· {{ agent.cert.on_link ? 'used on this link' : 'not seen on the link' }}{{ agent.cert.mode === 'required' ? ', required' : '' }}</span></dd>
            <dd v-else class="faint">none yet{{ agent.cert.mode === 'required' ? ' (required: the agent can’t link)' : '' }}</dd>
          </template>
          <dt>enrolled</dt><dd :title="stamp(agent.enrolled_at)">{{ ago(agent.enrolled_at) }} <span class="faint">(token by {{ agent.enrolled_by }})</span></dd>
        </dl>
      </div>
      <div class="card">
        <strong>Metrics</strong> <span class="small faint">{{ agent.metrics.at ? ago(agent.metrics.at) : 'none yet' }}</span>
        <dl class="kv small mt-2">
          <dt>cpu</dt><dd>{{ agent.metrics.cpu_percent ?? '—' }} % <span class="faint">load {{ agent.metrics.load1 ?? '—' }} / {{ agent.metrics.load5 ?? '—' }} / {{ agent.metrics.load15 ?? '—' }}</span></dd>
          <dt>memory</dt><dd>{{ pct(agent.metrics.mem_total - agent.metrics.mem_available, agent.metrics.mem_total) }} <span class="faint">of {{ gb(agent.metrics.mem_total) }}</span></dd>
          <dt>/</dt><dd>{{ pct(agent.metrics.root_used, agent.metrics.root_total) }} <span class="faint">of {{ gb(agent.metrics.root_total) }}</span></dd>
          <dt>up</dt><dd>{{ days(agent.metrics.uptime) }}</dd>
        </dl>
        <p class="small faint m-0">History lives in VictoriaMetrics: scrape <code>/api/agents/metrics</code>.</p>
      </div>
    </div>

    <div class="card">
      <div class="row" style="gap: 8px; flex-wrap: wrap">
        <strong style="margin-right: auto">Jobs</strong>
        <template v-if="mayEdit">
          <button class="btn small" :disabled="busy" @click="run('discovery.services')"><RefreshCw :size="14" /> Discover services</button>
          <button class="btn small" :disabled="busy" @click="run('patch.report')">Report updates</button>
          <button class="btn small" :disabled="busy" @click="run('facts')">Facts</button>
          <button class="btn small" :disabled="busy" @click="run('ping')">Ping</button>
        </template>
      </div>
      <div v-if="mayEdit && (canRun('patch') || canRun('ansible'))" class="row mt-2" style="gap: 12px; flex-wrap: wrap">
        <template v-if="canRun('patch')">
          <button class="btn small danger" :disabled="busy" @click="run('patch.apply', { reboot })"><Play :size="14" /> Install updates</button>
          <label v-if="canRun('reboot')" class="small"><input v-model="reboot" type="checkbox"> reboot if needed</label>
        </template>
        <template v-if="canRun('ansible')">
          <input v-model="playbook" class="mono small" style="width: 220px" aria-label="Playbook in the site's git repository">
          <label class="small"><input v-model="checkOnly" type="checkbox"> check only (--check --diff)</label>
          <button class="btn small danger" :disabled="busy" @click="run('ansible.run', { playbook, check: checkOnly })"><Play :size="14" /> Run playbook</button>
        </template>
      </div>
      <table v-if="data.jobs.length" class="mt-2">
        <thead><tr><th>Job</th><th>Status</th><th class="hide-m">By</th><th>When</th></tr></thead>
        <tbody>
          <tr v-for="j in data.jobs" :key="j.id">
            <td class="mono small">{{ j.type }}</td>
            <td><span :class="['st', statusClass(j.status)]">{{ j.status }}</span> <span v-if="j.error" class="small muted">{{ j.error }}</span></td>
            <td class="hide-m small muted">{{ j.created_by }}</td>
            <td class="small muted" :title="stamp(j.created_at)">{{ ago(j.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="disc" class="card">
      <strong>Listening ports</strong> <span class="small faint">discovered {{ ago(new Date(disc.collected_at * 1000).toISOString()) }}</span>
      <p v-for="n in disc.notes || []" :key="n" class="hint small">{{ n }}</p>
      <div class="table-wrap"><table class="mt-2">
        <thead><tr><th>Port</th><th>Address</th><th>Process</th><th class="hide-m">User</th><th class="hide-m">Unit / container</th><th class="hide-m">Command line</th></tr></thead>
        <tbody>
          <tr v-for="s in disc.listeners" :key="`${s.proto}${s.address}${s.port}`">
            <td class="mono">{{ s.port }}/{{ s.proto }}</td>
            <td class="mono small">{{ s.address }}</td>
            <td class="small"><template v-for="p in s.pids" :key="p.pid">{{ p.name }} <span class="faint">({{ p.pid }}<template v-if="procs[p.pid]?.parent?.name">, from {{ procs[p.pid].parent.name }} {{ procs[p.pid].parent.pid }}</template>)</span> </template>
              <span v-if="!s.pids.length" class="faint">unknown</span></td>
            <td class="hide-m small">{{ s.pids.map((p) => procs[p.pid]?.user).filter(Boolean).join(', ') }}</td>
            <td class="hide-m small mono">{{ s.container ? `${s.container.name} (${s.container.image})` : (s.units || []).join(', ') }}</td>
            <td class="hide-m small mono cmdline">{{ s.pids.map((p) => procs[p.pid]?.cmdline).filter(Boolean)[0] || '' }}</td>
          </tr>
        </tbody>
      </table></div>
      <details class="mt-2">
        <summary class="small">Running services ({{ disc.units.length }})</summary>
        <table class="small">
          <thead><tr><th>Unit</th><th>PID</th><th>User</th><th class="hide-m">Description</th></tr></thead>
          <tbody><tr v-for="u in disc.units" :key="u.unit"><td class="mono">{{ u.unit }}</td><td>{{ u.pid || '' }}</td><td>{{ u.user }}</td><td class="hide-m muted">{{ u.description }}</td></tr></tbody>
        </table>
      </details>
      <details v-if="disc.containers.length" class="mt-2">
        <summary class="small">Containers ({{ disc.containers.length }})</summary>
        <table class="small">
          <thead><tr><th>Name</th><th>Image</th><th class="hide-m">Status</th><th>Ports</th></tr></thead>
          <tbody><tr v-for="c in disc.containers" :key="c.id"><td class="mono">{{ c.name }}</td><td class="mono">{{ c.image }}</td><td class="hide-m muted">{{ c.status }}</td>
            <td class="mono">{{ c.ports.map((p) => `${p.port}→${p.container_port}/${p.proto}`).join(', ') }}</td></tr></tbody>
        </table>
      </details>
    </div>

    <div class="card">
      <strong>Settings</strong>
      <div class="row mt-2" style="gap: 16px; flex-wrap: wrap">
        <label class="small">Discover every <input type="number" min="0" :value="agent.discovery_every" :disabled="!mayEdit" style="width: 70px"
                                                    @change="save({ discovery_every: +$event.target.value })"> min</label>
        <label class="small">Report updates every <input type="number" min="0" :value="agent.patches_every" :disabled="!mayEdit" style="width: 70px"
                                                         @change="save({ patches_every: +$event.target.value })"> min</label>
        <span class="small faint">0 turns it off.</span>
      </div>
      <div class="mt-2 small">
        Writes (both ends must allow them):
        <label v-for="c in data.capabilities" :key="c" style="margin-left: 10px">
          <input type="checkbox" :checked="agent.capabilities.includes(c)" :disabled="!isAdmin" @change="toggleCap(c)"> {{ c }}
          <span class="faint">({{ agent.host_allow.includes(c) ? 'host allows' : 'host doesn’t allow' }})</span>
        </label>
        <label v-if="isAdmin" style="margin-left: 10px"><input type="checkbox" :checked="agent.allow_root" @change="save({ allow_root: !agent.allow_root })"> root allowed</label>
      </div>
      <button v-if="mayEdit" class="btn small danger mt-2" @click="revoke"><ShieldOff :size="14" /> Revoke</button>
    </div>
  </template>
</template>

<style scoped>
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.cmd { white-space: pre-wrap; word-break: break-all; padding: 8px; border-radius: 6px; background: var(--bg-2, rgba(127, 127, 127, 0.1)); cursor: pointer; }
.cmdline { max-width: 360px; overflow-wrap: anywhere; }
.st { font-size: 12px; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.st.ok { color: var(--ok); }
.st.bad { color: var(--err); }
.st.warn { color: var(--warn); }
</style>
