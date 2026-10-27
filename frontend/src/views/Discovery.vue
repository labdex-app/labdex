<script setup>
import { X } from 'lucide-vue-next'
import FilterSelect from '../components/FilterSelect.vue'
import { onActivated, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, enc } from '../api'
import HostPicker from '../components/HostPicker.vue'
import ZoneChip from '../components/ZoneChip.vue'
import { ago, can, changed, state, toast } from '../store'

const route = useRoute()
const router = useRouter()
const tab = ref(String(route.query.tab || 'services'))
const summary = ref(null)
const scanners = ref([])
const f = reactive({ status: String(route.query.status || 'undocumented,unknown-host'), q: '', page: 1, closed: false })
const data = ref({ items: [], total: 0, page: 1, per_page: 50 })
const loading = ref(false)
const busy = reactive({})
let timer

const STATUS = { documented: ['ok', 'Documented'], undocumented: ['warn', 'Not in inventory'], 'unknown-host': ['err', 'Unknown host'] }
const SEV = { ok: 'info', warn: 'warning', err: 'error' }

async function loadSummary() {
  summary.value = await api.get('/api/discovery/summary')
  if (can('integration', 'view')) {
    const ints = await api.get('/api/integrations').catch(() => [])
    scanners.value = ints.filter((i) => i.enabled && (state.nav?.modules || []).some((m) => m.integration === i.name && m.provides.includes('discovery')))
  }
}

async function load() {
  loading.value = true
  const qs = new URLSearchParams({ page: f.page, q: f.q })
  let url
  if (tab.value === 'services') {
    qs.set('status', f.status)
    if (f.closed) qs.set('closed', '1')
    url = `/api/discovery/services?${qs}`
  } else url = `/api/discovery/${{ unknown: 'unknown-hosts', missing: 'missing', macs: 'macs' }[tab.value]}?${qs}`
  try { data.value = await api.get(url) } catch (e) { toast(e.message, 'error') } finally { loading.value = false }
}

onMounted(() => { loadSummary().catch((e) => toast(e.message, 'error')); load() })
let shown = false // kept alive: back on this page, refresh in the background (rows stay where you were)
onActivated(() => { if (shown) { loadSummary().catch(() => {}); load() } shown = true })
watch(() => f.q, () => { clearTimeout(timer); timer = setTimeout(() => { f.page = 1; load() }, 250) })

function setTab(t) {
  tab.value = t
  f.page = 1
  router.replace({ query: { tab: t } })
  load()
}
function setStatus(s) { f.status = s; f.page = 1; if (tab.value !== 'services') setTab('services'); else load() }

async function done(msg) {
  toast(msg)
  await Promise.all([load(), loadSummary(), changed()])
}

// one click: declare the port on the host that has the address
async function adopt(s, hostName) {
  busy[s.id] = true
  try {
    const h = await api.post(`/api/discovery/services/${s.id}/adopt`, hostName ? { host: hostName } : {})
    await done(`${s.port}/${s.proto} added to ${h.name}`)
  } catch (e) { toast(e.message, 'error') } finally { busy[s.id] = false }
}

async function assign(u, hostName, withServices = true) {
  busy[u.ip] = true
  try {
    const h = await api.post('/api/discovery/addresses/assign', { ip: u.ip, host: hostName, with_services: withServices })
    await done(`${u.ip} added to ${h.name}`)
  } catch (e) { toast(e.message, 'error') } finally { busy[u.ip] = false }
}

async function forget(ip) {
  try { await api.del(`/api/discovery/addresses/${enc(ip)}`); await done(`Forgot ${ip}`) } catch (e) { toast(e.message, 'error') }
}

// MACs to fill in: interfaces without a MAC whose address was seen with one
const picked = reactive({})
const key = (m) => `${m.host}|${m.ip}`
async function applyMacs(rows) {
  const items = rows.filter((m) => m.can_edit).map((m) => ({ host: m.host, ip: m.ip, mac: m.mac }))
  if (!items.length) return
  try {
    const r = await api.post('/api/discovery/macs/apply', { items })
    for (const m of rows) delete picked[key(m)]
    await done(`${r.applied} MAC address(es) filled in`)
  } catch (e) { toast(e.message, 'error') }
}
const pickedRows = () => data.value.items.filter((m) => picked[key(m)])

const newHost = (ip, network, mac, hint, vendor) => ({ path: '/hosts/new', query: Object.fromEntries(Object.entries({ ip, network, mac, hint, vendor }).filter(([, v]) => v)) })
const product = (s) => [s.product, s.version].filter(Boolean).join(' ')
const pages = () => Math.max(1, Math.ceil(data.value.total / data.value.per_page))
function go(p) { f.page = p; load() }
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Discovery</h1>
      <span class="sub">What's actually there, compared with the inventory.</span>
      <span class="grow"></span>
      <RouterLink v-for="s in scanners" :key="s.name" :to="`/integrations/${s.name}`" class="btn small">
        {{ s.name }} <span v-if="s.last_job" class="faint">· {{ ago(s.last_job.created_at) }}</span>
      </RouterLink>
    </div>

    <div v-if="summary" class="stats">
      <button :class="['stat', summary.undocumented ? 'warn' : '']" @click="setStatus('undocumented')"><b>{{ summary.undocumented }}</b><span>ports not in the inventory</span></button>
      <button :class="['stat', summary.unknown_host ? 'err' : '']" @click="setStatus('unknown-host')"><b>{{ summary.unknown_host }}</b><span>ports on unknown hosts</span></button>
      <button :class="['stat', summary.unknown_hosts ? 'err' : '']" @click="setTab('unknown')"><b>{{ summary.unknown_hosts }}</b><span>unknown addresses</span></button>
      <button :class="['stat', summary.missing ? 'warn' : '']" @click="setTab('missing')"><b>{{ summary.missing }}</b><span>declared, not seen</span></button>
      <button v-if="summary.macs" class="stat info" @click="setTab('macs')"><b>{{ summary.macs }}</b><span>MACs to fill in</span></button>
      <button :class="['stat', summary.documented ? 'ok' : '']" @click="setStatus('documented')"><b>{{ summary.documented }}</b><span>documented</span></button>
    </div>

    <div class="tabs">
      <a href="#" :class="{ 'router-link-active': tab === 'services' }" @click.prevent="setTab('services')">Open ports</a>
      <a href="#" :class="{ 'router-link-active': tab === 'unknown' }" @click.prevent="setTab('unknown')">Unknown addresses</a>
      <a href="#" :class="{ 'router-link-active': tab === 'missing' }" @click.prevent="setTab('missing')">Not seen</a>
      <a href="#" :class="{ 'router-link-active': tab === 'macs' }" @click.prevent="setTab('macs')">MACs</a>
    </div>

    <div class="filters">
      <template v-if="tab === 'services'">
        <FilterSelect :model-value="f.status" label="Show" any="" :options="[{ value: 'undocumented,unknown-host', label: 'To review' }, ...Object.entries(STATUS).map(([k, v]) => ({ value: k, label: v[1] })), { value: 'all', label: 'All' }]" @update:model-value="setStatus" />
        <label class="check small" style="margin-left: 8px"><input v-model="f.closed" type="checkbox" @change="load"> closed too</label>
      </template>
      <span class="grow"></span>
      <input v-model="f.q" type="search" placeholder="Filter…" style="max-width: 240px" aria-label="Filter">
    </div>

    <div class="card flush">
      <div class="table-wrap">
        <table v-if="tab === 'services'">
          <thead><tr><th>Address</th><th>Port</th><th>Service</th><th>Host</th><th>Zone</th><th>Status</th><th>Seen</th><th></th></tr></thead>
          <tbody>
            <tr v-for="s in data.items" :key="s.id" :class="{ faint: s.state !== 'open' }">
              <td class="mono">{{ s.ip }}<div v-if="s.mac" class="small faint" :title="s.vendor">{{ s.mac }}</div></td>
              <td class="mono">{{ s.port }}/{{ s.proto }}</td>
              <td>{{ s.service || '?' }} <div v-if="product(s)" class="small faint">{{ product(s) }}</div></td>
              <td>
                <RouterLink v-if="s.host" :to="`/hosts/${s.host}`" class="mono">{{ s.fun || s.host }}</RouterLink>
                <span v-else class="faint">—</span>
                <div v-if="s.declared_as" class="small muted">as {{ s.declared_as }}</div>
              </td>
              <td><ZoneChip v-if="s.zone" :zone="s.zone" /></td>
              <td><span class="sev" :class="`sev-${SEV[STATUS[s.status][0]]}`">{{ STATUS[s.status][1] }}</span></td>
              <td class="small nowrap">{{ ago(s.last_seen) }} <div class="faint">{{ s.source }}</div></td>
              <td class="right nowrap act">
                <template v-if="can('host', 'edit')">
                  <button v-if="s.status === 'undocumented'" class="btn small primary" :disabled="busy[s.id]" @click="adopt(s)">+ Add to {{ s.fun || s.host }}</button>
                  <template v-else-if="s.status === 'unknown-host'">
                    <RouterLink :to="newHost(s.ip, s.network, '', s.service || String(s.port))" class="btn small">New host</RouterLink>
                    <HostPicker placeholder="or add to host…" :zone="s.zone" @pick="(h) => adopt(s, h)" />
                  </template>
                </template>
              </td>
            </tr>
          </tbody>
        </table>

        <table v-else-if="tab === 'unknown'">
          <thead><tr><th>Address</th><th>MAC</th><th>Name / vendor</th><th>Network</th><th>Seen by</th><th>Seen</th><th></th></tr></thead>
          <tbody>
            <tr v-for="u in data.items" :key="u.ip">
              <td class="mono"><span class="dot" :class="u.up ? 'up' : ''"></span>{{ u.ip }}</td>
              <td class="mono small">{{ u.mac || '—' }}<div v-if="u.mac_source" class="faint">{{ u.mac_source }}</div></td>
              <td>{{ u.hostname || '—' }} <div v-if="u.vendor" class="small faint">{{ u.vendor }}</div></td>
              <td><span v-if="u.network" class="mono">{{ u.network }}</span> <ZoneChip v-if="u.zone" :zone="u.zone" /></td>
              <td class="small">{{ u.source }}</td>
              <td class="small">{{ ago(u.last_seen) }}</td>
              <td class="right nowrap act">
                <template v-if="can('host', 'edit')">
                  <RouterLink :to="newHost(u.ip, u.network, u.mac, u.hostname, u.vendor)" class="btn small">New host</RouterLink>
                  <HostPicker placeholder="or add to host…" :zone="u.zone" @pick="(h) => assign(u, h)" />
                </template>
                <button v-if="can('discovery', 'admin')" class="btn small ghost" title="Forget this address" aria-label="Forget this address" @click="forget(u.ip)"><X :size="14" /></button>
              </td>
            </tr>
          </tbody>
        </table>

        <template v-else-if="tab === 'macs'">
          <div v-if="data.items.length" class="row macbar">
            <span class="small muted">Interfaces in the inventory without a MAC, where an ARP table, a DHCP lease or nmap saw one. Kea reservations need them.</span>
            <span class="grow"></span>
            <button class="btn small" :disabled="!pickedRows().length" @click="applyMacs(pickedRows())">Fill in selected</button>
            <button class="btn small primary" @click="applyMacs(data.items)">Fill in all on this page</button>
          </div>
          <table>
            <thead><tr><th></th><th>Host</th><th>Interface</th><th>Address</th><th>MAC seen</th><th>From</th><th>Seen</th></tr></thead>
            <tbody>
              <tr v-for="m in data.items" :key="key(m)">
                <td><input v-model="picked[key(m)]" type="checkbox" :disabled="!m.can_edit" :aria-label="`Select ${m.host} ${m.ip}`"></td>
                <td><RouterLink :to="`/hosts/${m.host}?tab=network`" class="mono">{{ m.fun || m.host }}</RouterLink></td>
                <td>{{ m.network || m.label || '—' }}</td>
                <td class="mono">{{ m.ip }}</td>
                <td class="mono">{{ m.mac }}<div v-if="m.vendor" class="small faint">{{ m.vendor }}</div></td>
                <td class="small">{{ m.source }}</td>
                <td class="small">{{ ago(m.seen) }}</td>
              </tr>
            </tbody>
          </table>
        </template>

        <table v-else>
          <thead><tr><th>Host</th><th>Service</th><th>Port</th><th>Addresses scanned</th></tr></thead>
          <tbody>
            <tr v-for="m in data.items" :key="m.host + m.service">
              <td><RouterLink :to="`/hosts/${m.host}`" class="mono">{{ m.fun || m.host }}</RouterLink></td>
              <td>{{ m.service }}</td><td class="mono">{{ m.port }}/{{ m.proto }}</td>
              <td class="mono small">{{ m.ips.join(', ') }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!data.items.length && !loading" class="empty">
          {{ { services: 'Nothing to review here.', unknown: 'Every address seen is in the inventory.', macs: 'No MACs to fill in: every interface that was seen has one, or none was seen with a MAC.' }[tab] || 'Every declared service on a scanned host answered.' }}
          <template v-if="!summary?.open && !summary?.unknown_hosts && can('integration', 'admin')"><br>Nothing discovered yet: add nmap, OPNsense (ARP, DHCP leases) or Technitium (records) under <RouterLink to="/plugins">Plugins</RouterLink>.</template>
        </div>
      </div>
    </div>
    <div v-if="data.total > data.per_page" class="row pager">
      <button class="btn small" :disabled="f.page <= 1" @click="go(f.page - 1)">‹ Prev</button>
      <span class="small muted">Page {{ f.page }} of {{ pages() }} · {{ data.total }} rows</span>
      <button class="btn small" :disabled="f.page >= pages()" @click="go(f.page + 1)">Next ›</button>
    </div>
  </div>
</template>

<style scoped>
button.stat { font: inherit; color: inherit; text-align: left; cursor: pointer; }
button.stat:hover { border-color: var(--faint); }
.act { display: flex; gap: 6px; justify-content: flex-end; align-items: center; }
.pager { justify-content: center; margin-top: 14px; }
.macbar { padding: 12px 16px; border-bottom: 1px solid var(--line); gap: 8px; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--faint); margin-right: 7px; opacity: .5; }
.dot.up { background: var(--ok); opacity: 1; }
</style>
