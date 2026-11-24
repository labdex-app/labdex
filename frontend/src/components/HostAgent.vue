<script setup>
// The host's agent: enrol it, see whether it's linked and how the host is doing (48 h of metrics),
// what it discovered (ports, the processes behind them, systemd units, containers) and adopt those
// as services, its jobs, and its settings. Writes (updates, reboots, Ansible) appear only once an
// administrator switched them on here and the host allows them.
import { Box, Cpu, KeyRound, Play, Plus, RefreshCw, ShieldCheck, ShieldOff } from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, enc } from '../api'
import { confirm } from '../confirm'
import { ago, can, copy, stamp, state, toast } from '../store'
import JobBadge from './JobBadge.vue'
import Loading from './Loading.vue'
import Spark from './Spark.vue'

const props = defineProps({ host: { type: String, required: true } })
const data = ref(null)
const enrolment = ref(null) // {token, command, agent, python: {command}, go?, expires_at, url, url_note, reroll}
const allowRoot = ref(false)
const reboot = ref(false)
const playbook = ref('ansible/site.yml')
const checkOnly = ref(true)
const busy = ref('')
const found = ref('ports') // ports, units, containers
const isAdmin = computed(() => !!state.meta?.is_admin)
const mayEdit = computed(() => can('host', 'edit'))
const agent = computed(() => data.value?.agent)
const disc = computed(() => data.value?.discovery)
const declared = computed(() => data.value?.declared || { ports: {}, units: {} })
const procs = computed(() => Object.fromEntries((disc.value?.processes || []).map((p) => [p.pid, p])))
const hist = computed(() => data.value?.history || [])
const series = (k) => hist.value.map((h) => ({ at: h.at, v: h[k] }))

async function load() {
  try { data.value = await api.get(`/api/hosts/${enc(props.host)}/agent`) } catch (x) { toast(x.message, 'error') }
}
watch(() => props.host, () => { enrolment.value = null; load() }, { immediate: true })
const timer = setInterval(() => { if (!document.hidden) load() }, 10000)
onBeforeUnmount(() => clearInterval(timer))

async function act(key, fn) {
  busy.value = key
  try { await fn() } catch (x) { toast(x.message, 'error') } finally { busy.value = '' }
}
const enrol = () => act('enrol', async () => {
  enrolment.value = await api.post(`/api/hosts/${enc(props.host)}/agent/token`, { allow_root: allowRoot.value })
})
async function reroll() {
  if (!(await confirm({
    title: `Reroll the agent key on ${props.host}?`,
    body: 'You get a one-time command to run on the host. When it runs, the host gets a new key and the current one stops working. Until then, nothing changes.',
    action: 'Get the command',
  }))) return
  act('reroll', async () => {
    enrolment.value = { ...(await api.post(`/api/hosts/${enc(props.host)}/agent/token`, { reroll: true })), reroll: true }
  })
}
async function renew() {
  if (!(await confirm({
    title: `Renew the client certificate of ${props.host}'s agent?`,
    body: 'The agent gets a new certificate over its link now. The current one keeps working until the agent links with the new one.',
    action: 'Renew',
  }))) return
  act('renew', async () => {
    await api.post(`/api/hosts/${enc(props.host)}/agent/cert`)
    toast('New certificate asked for. The agent uses it from its next link.')
    await load()
  })
}
const run = (type, params = {}) => act(type, async () => {
  await api.post(`/api/hosts/${enc(props.host)}/agent/jobs`, { type, params })
  toast(`${type} queued`)
  await load()
})
async function save(patch) {
  try { await api.patch(`/api/hosts/${enc(props.host)}/agent`, patch); await load() } catch (x) { toast(x.message, 'error') }
}
function toggleCap(c) {
  const caps = new Set(agent.value.capabilities)
  caps.has(c) ? caps.delete(c) : caps.add(c)
  save({ capabilities: [...caps] })
}
async function revoke() {
  if (!(await confirm({ title: `Revoke the agent on ${props.host}?`, body: 'Its key stops working at once and its link closes. To use an agent here again, enrol it anew.', action: 'Revoke', danger: true }))) return
  act('revoke', async () => { await api.del(`/api/hosts/${enc(props.host)}/agent`); enrolment.value = null; await load() })
}
const adopt = (what, key) => act(`adopt:${key}`, async () => {
  const s = await api.post(`/api/hosts/${enc(props.host)}/agent/adopt`, what)
  toast(`${s.name} added to ${props.host}'s services`)
  await load()
})

const gb = (n) => (n ? `${(n / 1024 ** 3).toFixed(1)} GB` : '—')
const pct = (used, total) => (total ? Math.round((100 * used) / total) : null)
const uptime = (s) => (s ? (s >= 86400 ? `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h` : `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} m`) : '—')
const rate = (b) => (b == null ? '—' : b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB/s` : b >= 1024 ? `${Math.round(b / 1024)} kB/s` : `${b} B/s`)
const last = (k) => { for (let i = hist.value.length - 1; i >= 0; i--) if (hist.value[i][k] != null) return hist.value[i][k]; return null }
const canRun = (cap) => agent.value?.capabilities.includes(cap) && agent.value?.host_allow.includes(cap)
const certDays = computed(() => (agent.value?.cert?.not_after ? Math.floor((new Date(agent.value.cert.not_after) - Date.now()) / 86400e3) : null))
const certOn = computed(() => agent.value?.cert && agent.value.cert.mode !== 'off')
const m = computed(() => agent.value?.metrics || {})
const listenerKey = (s) => `${s.port}/${s.proto}`
const procOf = (s) => s.pids.map((p) => procs.value[p.pid]).find(Boolean)
const containers = computed(() => disc.value?.containers || [])
const CAP_TEXT = { patch: 'Install updates', reboot: 'Reboot after updates', ansible: 'Run Ansible playbooks' }
</script>

<template>
  <Loading v-if="data === null" />

  <!-- no agent yet: what it is, and the command -->
  <div v-else-if="!agent" class="card empty agent-empty">
    <div class="e-ic"><Cpu :size="18" /></div>
    <div class="e-t">No agent on {{ host }}</div>
    <p>The agent is a small service that dials out to labdex (the host needs no open port). It reports metrics, listening ports,
      services and updates, and runs only signed jobs from a fixed list. It stays read-only unless you switch writes on.</p>
    <div v-if="mayEdit && !enrolment" class="e-acts">
      <button class="btn primary" :disabled="!!busy" @click="enrol">Enrol an agent</button>
      <label v-if="isAdmin" class="check"><input v-model="allowRoot" type="checkbox"> run as root (needed for Ansible)</label>
    </div>
  </div>

  <template v-else>
    <!-- the numbers, with 48 hours behind them -->
    <section class="card flush astats">
      <div class="cells">
        <div class="cell">
          <span class="label">Link</span>
          <b :class="agent.linked ? 'c-ok' : 'c-warn'">{{ agent.linked ? 'linked' : 'not linked' }}</b>
          <span class="small faint" :title="stamp(agent.last_seen)">{{ agent.last_seen ? `seen ${ago(agent.last_seen)}` : 'never seen' }}</span>
        </div>
        <div class="cell">
          <span class="label">CPU</span>
          <b>{{ m.cpu_percent != null ? Math.round(m.cpu_percent) : '—' }}<small>%</small></b>
          <Spark :points="series('cpu')" :max="100" label="CPU" />
        </div>
        <div class="cell">
          <span class="label">Memory</span>
          <b>{{ pct(m.mem_total - m.mem_available, m.mem_total) ?? '—' }}<small>% of {{ gb(m.mem_total) }}</small></b>
          <Spark :points="series('mem')" :max="100" label="Memory" />
        </div>
        <div class="cell">
          <span class="label">Disk /</span>
          <b>{{ pct(m.root_used, m.root_total) ?? '—' }}<small>% of {{ gb(m.root_total) }}</small></b>
          <Spark :points="series('disk')" :max="100" label="Disk" />
        </div>
        <div class="cell">
          <span class="label">Network in</span>
          <b class="rate">{{ rate(last('rx')) }}</b>
          <Spark :points="series('rx')" label="Network in" unit=" B/s" />
        </div>
        <div class="cell">
          <span class="label">Up</span>
          <b class="rate">{{ uptime(m.uptime) }}</b>
          <span class="small faint">load {{ m.load1 ?? '—' }} / {{ m.load5 ?? '—' }} / {{ m.load15 ?? '—' }}</span>
        </div>
      </div>
      <p class="small faint m-0 foot">48 hours kept here, a sample every 5 minutes. For longer, scrape <code>/api/agents/metrics</code> into VictoriaMetrics.</p>
    </section>

    <!-- a command to run on the host: a reroll -->
    <div v-if="enrolment" class="card cmdcard">
      <div class="sect-head"><h3>{{ enrolment.reroll ? 'New key: run this on the host' : 'Run this on the host' }}</h3><span class="grow"></span>
        <button class="btn small ghost" @click="enrolment = null">Close</button></div>
      <p class="small m-0">On {{ host }}, before {{ stamp(enrolment.expires_at) }}. The token works once.
        <template v-if="enrolment.reroll"> The current key works until this runs.</template></p>
      <pre class="mono small cmd">{{ enrolment.command }}</pre>
      <div class="row" style="gap: 8px"><button class="btn small" @click="copy(enrolment.command)">Copy</button></div>
      <p v-if="enrolment.url_note" class="warnbox small mt-2 mb-0">{{ enrolment.url_note }}
        <RouterLink v-if="isAdmin" to="/admin/general">Settings › General</RouterLink></p>
      <details v-if="enrolment.agent === 'go'" class="small mt-2">
        <summary>The Python agent instead</summary>
        <p class="faint m-0">Needs Python 3.11 or newer on the host, and runs from its own venv in <code>/opt/labdex-agent/venv</code>.</p>
        <pre class="mono small cmd">{{ enrolment.python.command }}</pre>
        <div class="row" style="gap: 8px"><button class="btn small" @click="copy(enrolment.python.command)">Copy</button></div>
      </details>
    </div>

    <div class="two">
      <!-- who the agent is -->
      <section class="card flush">
        <div class="sect-head pad">
          <h3>Agent <span class="mono small faint">{{ agent.id }}</span></h3>
          <span class="grow"></span>
          <template v-if="mayEdit">
            <button v-if="certOn" class="btn small" :disabled="!!busy || !agent.linked" :title="agent.linked ? '' : 'Needs a link'" @click="renew"><ShieldCheck :size="14" /> Renew certificate</button>
            <button class="btn small" :disabled="!!busy" @click="reroll"><KeyRound :size="14" /> Reroll key</button>
          </template>
        </div>
        <dl class="rows">
          <dt>Runs as</dt><dd>{{ agent.root ? 'root' : agent.facts.user || 'labdex-agent' }}<span v-if="agent.allow_root" class="faint"> · root allowed</span></dd>
          <dt>Version</dt><dd class="mono">{{ agent.version || '—' }} <span class="faint">on {{ agent.hostname }}</span></dd>
          <template v-if="agent.facts.os"><dt>OS</dt><dd>{{ agent.facts.os }} <span class="faint">· {{ agent.facts.kernel }}</span></dd></template>
          <dt>Address</dt><dd class="mono">{{ agent.address || '—' }}</dd>
          <template v-if="certOn">
            <dt>Certificate</dt>
            <dd v-if="agent.cert.issued" :title="agent.cert.sha256">
              <span :class="certDays !== null && certDays < 30 ? 'c-warn' : ''">until {{ new Date(agent.cert.not_after).toLocaleDateString() }}</span>
              <span class="faint"> · {{ agent.cert.on_link ? 'used on this link' : 'not seen on the link' }}{{ agent.cert.mode === 'required' ? ' · required' : '' }}</span></dd>
            <dd v-else class="faint">none yet{{ agent.cert.mode === 'required' ? ' (required: the agent can’t link)' : '' }}</dd>
          </template>
          <dt>Enrolled</dt><dd :title="stamp(agent.enrolled_at)">{{ ago(agent.enrolled_at) }} <span class="faint">· token by {{ agent.enrolled_by }}</span></dd>
        </dl>
      </section>

      <!-- what it did -->
      <section class="card flush">
        <div class="sect-head pad">
          <h3>Jobs</h3><span class="grow"></span>
          <template v-if="mayEdit">
            <button class="btn small" :disabled="!!busy" @click="run('discovery.services')"><RefreshCw :size="14" /> Discover</button>
            <button class="btn small" :disabled="!!busy" @click="run('patch.report')">Report updates</button>
            <button class="btn small ghost" :disabled="!!busy" @click="run('facts')">Facts</button>
            <button class="btn small ghost" :disabled="!!busy" @click="run('ping')">Ping</button>
          </template>
        </div>
        <div v-if="mayEdit && (canRun('patch') || canRun('ansible'))" class="writes pad">
          <template v-if="canRun('patch')">
            <button class="btn small primary" :disabled="!!busy" @click="run('patch.apply', { reboot })"><Play :size="14" /> Install updates</button>
            <label v-if="canRun('reboot')" class="check small"><input v-model="reboot" type="checkbox"> reboot if needed</label>
          </template>
          <template v-if="canRun('ansible')">
            <input v-model="playbook" class="mono small" style="width: 200px" aria-label="Playbook in the site's git repository">
            <label class="check small"><input v-model="checkOnly" type="checkbox"> check only</label>
            <button class="btn small primary" :disabled="!!busy" @click="run('ansible.run', { playbook, check: checkOnly })"><Play :size="14" /> Run playbook</button>
          </template>
        </div>
        <table v-if="data.jobs.length">
          <thead><tr><th>Job</th><th>Status</th><th class="hide-m">By</th><th class="r">When</th></tr></thead>
          <tbody>
            <tr v-for="j in data.jobs.slice(0, 8)" :key="j.id">
              <td class="mono">{{ j.type }}</td>
              <td><JobBadge :status="j.status" /><div v-if="j.error" class="small muted">{{ j.error }}</div></td>
              <td class="hide-m muted">{{ j.trigger === 'schedule' ? 'schedule' : j.created_by }}</td>
              <td class="r muted" :title="stamp(j.created_at)">{{ ago(j.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty-line pad">No jobs yet.</p>
      </section>
    </div>

    <!-- what it found, and taking it into the inventory -->
    <section v-if="disc" class="card flush">
      <div class="sect-head pad">
        <h3>Discovered</h3>
        <span class="small faint">{{ ago(new Date(disc.collected_at * 1000).toISOString()) }}</span>
        <span class="grow"></span>
        <div class="chips" role="tablist">
          <button class="chip" :class="{ on: found === 'ports' }" role="tab" :aria-selected="found === 'ports'" @click="found = 'ports'">Listening ports <span class="n">{{ disc.listeners.length }}</span></button>
          <button class="chip" :class="{ on: found === 'units' }" role="tab" :aria-selected="found === 'units'" @click="found = 'units'">Services <span class="n">{{ disc.units.length }}</span></button>
          <button v-if="containers.length" class="chip" :class="{ on: found === 'containers' }" role="tab" :aria-selected="found === 'containers'" @click="found = 'containers'">Containers <span class="n">{{ containers.length }}</span></button>
        </div>
      </div>
      <p v-for="n in disc.notes || []" :key="n" class="warnbox small mx">{{ n }}</p>
      <div v-if="found === 'ports'" class="table-wrap"><table>
        <thead><tr><th>Port</th><th>Address</th><th>Process</th><th class="hide-m">Unit / container</th><th class="hide-m">Command line</th><th class="r">In the inventory</th></tr></thead>
        <tbody>
          <tr v-for="s in disc.listeners" :key="`${s.proto}${s.address}${s.port}`">
            <td class="mono nowrap">{{ s.port }}/{{ s.proto }}</td>
            <td class="mono">{{ s.address }}</td>
            <td><template v-for="p in s.pids" :key="p.pid">{{ p.name }} </template><span v-if="!s.pids.length" class="faint">unknown</span>
              <div v-if="procOf(s)" class="small faint">{{ procOf(s).user }} · pid {{ procOf(s).pid }}<template v-if="procOf(s).parent?.name"> · from {{ procOf(s).parent.name }}</template></div></td>
            <td class="hide-m mono small">{{ s.container ? `${s.container.name} (${s.container.image})` : (s.units || []).join(', ') }}</td>
            <td class="hide-m mono small cmdline">{{ procOf(s)?.cmdline || '' }}</td>
            <td class="r nowrap">
              <RouterLink v-if="declared.ports[listenerKey(s)]" :to="{ query: { tab: 'services' } }" class="tag">{{ declared.ports[listenerKey(s)] }}</RouterLink>
              <button v-else-if="mayEdit" class="btn small" :disabled="!!busy" :title="`Declare ${listenerKey(s)} as a service of ${host}`"
                      @click="adopt({ port: s.port, proto: s.proto }, listenerKey(s))"><Plus :size="14" /> Add as service</button>
            </td>
          </tr>
        </tbody>
      </table></div>
      <div v-else-if="found === 'units'" class="table-wrap"><table>
        <thead><tr><th>Unit</th><th>PID</th><th>User</th><th class="hide-m">Description</th><th class="r">In the inventory</th></tr></thead>
        <tbody><tr v-for="u in disc.units" :key="u.unit">
          <td class="mono">{{ u.unit }}</td><td class="mono">{{ u.pid || '' }}</td><td>{{ u.user }}</td><td class="hide-m muted">{{ u.description }}</td>
          <td class="r nowrap">
            <RouterLink v-if="declared.units[u.unit]" :to="{ query: { tab: 'services' } }" class="tag">{{ declared.units[u.unit] }}</RouterLink>
            <button v-else-if="mayEdit" class="btn small" :disabled="!!busy" @click="adopt({ unit: u.unit }, u.unit)"><Plus :size="14" /> Add as service</button>
          </td>
        </tr></tbody>
      </table></div>
      <div v-else class="table-wrap"><table>
        <thead><tr><th>Name</th><th>Image</th><th class="hide-m">Status</th><th>Ports</th></tr></thead>
        <tbody><tr v-for="c in containers" :key="c.id"><td class="mono"><Box :size="13" class="faint" /> {{ c.name }}</td><td class="mono">{{ c.image }}</td><td class="hide-m muted">{{ c.status }}</td>
          <td class="mono">{{ c.ports.map((p) => `${p.port}→${p.container_port}/${p.proto}`).join(', ') }}</td></tr></tbody>
      </table></div>
    </section>

    <!-- how it runs -->
    <section class="card flush">
      <div class="sect-head pad"><h3>Settings</h3></div>
      <div class="set">
        <div class="set-row">
          <div><div>Discover services every</div><div class="small faint">Ports, processes, units and containers. 0 turns it off.</div></div>
          <label class="num"><input type="number" min="0" :value="agent.discovery_every" :disabled="!mayEdit" aria-label="Minutes between discoveries"
                                    @change="save({ discovery_every: +$event.target.value })"> min</label>
        </div>
        <div class="set-row">
          <div><div>Report updates every</div><div class="small faint">Pending packages and whether a reboot is needed. 0 turns it off.</div></div>
          <label class="num"><input type="number" min="0" :value="agent.patches_every" :disabled="!mayEdit" aria-label="Minutes between update reports"
                                    @change="save({ patches_every: +$event.target.value })"> min</label>
        </div>
        <div class="set-head label">Writes · both ends must allow them</div>
        <div v-for="c in data.capabilities" :key="c" class="set-row">
          <div><div>{{ CAP_TEXT[c] || c }} <span class="mono small faint">{{ c }}</span></div>
            <div class="small" :class="agent.host_allow.includes(c) ? 'c-ok' : 'faint'">{{ agent.host_allow.includes(c) ? 'the host allows it' : `the host doesn’t allow it (install with --allow "${c}")` }}</div></div>
          <label class="check" :title="isAdmin ? '' : 'Administrators switch writes on'"><input type="checkbox" :checked="agent.capabilities.includes(c)" :disabled="!isAdmin" :aria-label="CAP_TEXT[c] || c" @change="toggleCap(c)"></label>
        </div>
        <div class="set-row">
          <div><div>Root allowed</div><div class="small faint">An agent that runs as root may link. Ansible needs it.</div></div>
          <label class="check"><input type="checkbox" :checked="agent.allow_root" :disabled="!isAdmin" aria-label="Root allowed" @change="save({ allow_root: !agent.allow_root })"></label>
        </div>
        <div v-if="mayEdit" class="set-row danger-row">
          <div><div>Revoke the agent</div><div class="small faint">Its key stops working at once. To use an agent here again, enrol it anew.</div></div>
          <button class="btn small danger" :disabled="!!busy" @click="revoke"><ShieldOff :size="14" /> Revoke</button>
        </div>
      </div>
    </section>
  </template>

  <div v-if="!agent && enrolment" class="card cmdcard">
    <div class="sect-head"><h3>Run this on the host</h3></div>
    <p class="small m-0">On {{ host }} as root, before {{ stamp(enrolment.expires_at) }}. The token works once.</p>
    <pre class="mono small cmd">{{ enrolment.command }}</pre>
    <div class="row" style="gap: 8px"><button class="btn small" @click="copy(enrolment.command)">Copy</button></div>
    <p v-if="enrolment.url_note" class="warnbox small mt-2 mb-0">{{ enrolment.url_note }}
      <RouterLink v-if="isAdmin" to="/admin/general">Settings › General</RouterLink></p>
    <p class="small faint mb-0">Add <code>--allow "patch reboot ansible"</code> for the writes this host may accept; without it the agent stays read-only.</p>
    <details v-if="enrolment.agent === 'go'" class="small mt-2">
      <summary>The Python agent instead</summary>
      <p class="faint m-0">Needs Python 3.11 or newer on the host, and runs from its own venv in <code>/opt/labdex-agent/venv</code>.</p>
      <pre class="mono small cmd">{{ enrolment.python.command }}</pre>
      <div class="row" style="gap: 8px"><button class="btn small" @click="copy(enrolment.python.command)">Copy</button></div>
    </details>
  </div>
</template>

<style scoped>
.agent-empty { margin-bottom: 16px; }
.astats { margin-bottom: 16px; }
.cells { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.cell { display: flex; flex-direction: column; gap: 4px; padding: 14px 18px; border-left: 1px solid var(--line); min-width: 0; }
.cell:first-child { border-left: 0; }
.cell b { font: 500 calc(22px * var(--text))/1.2 var(--mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cell b small { font-size: calc(12px * var(--text)); color: var(--muted); font-weight: 400; margin-left: 2px; }
.cell b.rate { font-size: calc(17px * var(--text)); line-height: 1.6; }
.foot { padding: 10px 18px; }
.c-ok { color: var(--ok); }
.c-warn { color: var(--warn); }
.two { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); gap: 16px; margin-bottom: 16px; align-items: start; }
.two > .card { margin: 0; }
.two + .card { margin-top: 0; }
.pad { padding: 14px 18px; margin: 0; }
.sect-head.pad { border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.sect-head h3 { font-size: calc(14px * var(--text)); }
.rows { display: grid; grid-template-columns: max-content 1fr; margin: 0; }
.rows dt, .rows dd { padding: 10px 18px; border-bottom: 1px solid var(--line); margin: 0; }
.rows dt { color: var(--muted); }
.rows dd { padding-left: 0; overflow-wrap: anywhere; }
.rows dt:last-of-type, .rows dd:last-of-type { border-bottom: 0; }
.writes { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; border-bottom: 1px solid var(--line); }
.r { text-align: right; }
.nowrap { white-space: nowrap; }
.cmdline { max-width: 380px; overflow-wrap: anywhere; color: var(--muted); }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.mx { margin: 12px 18px 0; }
.cmdcard { margin-bottom: 16px; border-color: var(--accent); }
.cmd { white-space: pre-wrap; word-break: break-all; padding: 10px 12px; margin: 10px 0; border-radius: var(--r-ctl); background: var(--raise); border: 1px solid var(--line); }
.set-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 18px; border-bottom: 1px solid var(--line); }
.set-row:last-child { border-bottom: 0; }
.set-head { padding: 14px 18px 6px; border-bottom: 1px solid var(--line); }
.num { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; color: var(--muted); }
.num input { width: 76px; text-align: right; }
@media (max-width: 1100px) { .cells { grid-template-columns: repeat(3, minmax(0, 1fr)); } .cell:nth-child(4) { border-left: 0; } .cell:nth-child(n+4) { border-top: 1px solid var(--line); } .two { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 640px) { .cells { grid-template-columns: repeat(2, minmax(0, 1fr)); } .cell { border-left: 0; border-top: 1px solid var(--line); } .cell:nth-child(even) { border-left: 1px solid var(--line); } }
</style>
