<script setup>
import { useDraft, useUnsaved } from '../unsaved'
import { computed, onMounted, reactive, ref, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getCached, setQueryData, useQuery } from '../query'
import { api, ApiError } from '../api'
import {
  AppWindow, ArrowDownLeft, ArrowLeftRight, ArrowUpRight, Box, Cable, EthernetPort, ListPlus, Lock, MapPin, Merge, Pencil, Plus, RotateCcw, Route, Tag, Trash2, Waypoints, Wifi,
} from 'lucide-vue-next'
import { confirm } from '../confirm'
import ChipInput from '../components/ChipInput.vue'
import Modal from '../components/Modal.vue'
import ServiceForm from '../components/ServiceForm.vue'
import Copy from '../components/Copy.vue'
import Field from '../components/Field.vue'
import HistoryList from '../components/HistoryList.vue'
import HostModules from '../components/HostModules.vue'
import HostPicker from '../components/HostPicker.vue'
import RunList from '../components/RunList.vue'
import HostDashboard from '../components/HostDashboard.vue'
import SecretList from '../components/SecretList.vue'
import { stringify as toYaml } from 'yaml'
import ZoneChip from '../components/ZoneChip.vue'
import { ago, changed, stamp, state, tagColour, toast, zoneHex } from '../store'
import CableExtras from '../components/CableExtras.vue'
import PortMap from '../components/PortMap.vue'
import CableTrace from '../components/CableTrace.vue'
import ColourPicker from '../components/ColourPicker.vue'
import HostIcon from '../components/HostIcon.vue'
import { ICONS } from '../icons'

const props = defineProps({ name: { type: String, required: true } })
const router = useRouter()
const route = useRoute()
const isNew = computed(() => props.name === 'new')

const blank = () => ({
  name: '', fun: '', zone: '', zone_pinned: false, kind: 'device', vip_protocol: '', vip_members: [],
  status: 'active', function: '', hardware: '', vendor: '', model: '', icon: '', colour: '', cables: [], rules: [], runs_on: '', notes: '', tags: [],
  rack: '', rack_unit: null, rack_height: 1, rack_face: 'front',
  features: [], vars: '', interfaces: [], services: [], fun_names: [], fun_no_dns: [], records: [], dns_zone: '',
})
const form = reactive(blank())
const saved = ref('')
const derived = ref(null)
const preview = ref(null)
const networks = ref([])
const hostNames = ref([])
const rackList = ref([])
const IFACE_KINDS = ['ethernet', 'wifi', 'bond', 'bridge', 'vlan', 'virtual', 'wireguard', 'loopback', 'other']
import { CONNECTORS, MEDIA, SPEEDS, expandRange, isPhysical, setConnector, speedStep } from '../links'
const hypervisorIfaces = ref([])
// secondary addresses are one list on the server; the dialog shows them per family
const extraOf = (i, fam) => (i.extra_ips || []).filter((a) => (fam === 6) === a.includes(':'))
const setExtra = (i, fam, list) => { i.extra_ips = [...extraOf(i, fam === 4 ? 6 : 4), ...list] }
const extraErr = (fam) => { // the message starts with the address it's about
  const e = ifErr('extra_ips')
  return e && (fam === 6) === /^[0-9a-f]*:[0-9a-f:]*:/i.test(e) ? e : ''
}
const ifaceIps = (i) => [i.ip4, i.ip6, ...(i.extra_ips || [])].filter(Boolean)
function parentChoices(i) {
  const own = form.interfaces.filter((x) => x.label && x !== i).map((x) => ({ value: x.label, label: `${x.label}${x.kind ? ` (${x.kind})` : ''}` }))
  const hv = hypervisorIfaces.value.map((x) => ({ value: `${form.runs_on}:${x.label}`, label: `${form.runs_on}: ${x.label}${x.kind ? ` (${x.kind})` : ''}` }))
  const cur = i.parent && ![...own, ...hv].some((x) => x.value === i.parent) ? [{ value: i.parent, label: i.parent }] : []
  return [...own, ...hv, ...cur]
}
const srcQ = useQuery('/api/services/sources')
const peers = computed(() => {
  const s = srcQ.data.value || {}
  return ['any', ...(s.zones || []), ...(s.areas || []), ...(s.networks || []), ...(s.prefixes || []).map((p) => p.cidr), ...(s.aliases || [])]
})
// dialogs edit a copy; Done puts it into the form (saved with the host)
const clone = (x) => JSON.parse(JSON.stringify(x))
const ifDlg = ref(null)
const blankIf = () => ({ network: '', label: '', kind: '', parent: '', connector: '', speed: '', passthrough: '', extra_ips: [], ip4: '', ip6: '', ip6_token: '', ip6_overrides: {}, mac: '', primary: form.interfaces.length === 0, in_dns: null })
function openIf(idx) { ifDlg.value = { idx, i: idx < 0 ? blankIf() : clone(form.interfaces[idx]) } }
const ifErr = (k) => (ifDlg.value && ifDlg.value.idx >= 0 ? errors.value[`interfaces[${ifDlg.value.idx}].${k}`] : '')
function saveIf() {
  const { idx, i } = ifDlg.value
  if (i.primary) form.interfaces.forEach((x) => { x.primary = false })
  if (idx < 0) form.interfaces.push(i)
  else form.interfaces[idx] = i
  ifDlg.value = null
}
async function removeIf(idx) {
  const i = form.interfaces[idx]
  if (await confirm({ title: `Remove ${i.label || i.network || 'this interface'}?`, body: 'Its addresses and DNS records go when you save the host.', action: 'Remove', danger: true })) form.interfaces.splice(idx, 1)
}
const ifRows = computed(() => {
  const list = form.interfaces.map((i, idx) => ({ i, idx }))
  const byName = (l) => list.find((x) => x.i.label && x.i.label === l)
  const out = []
  const walk = (x, d) => { if (out.some((o) => o.idx === x.idx)) return; out.push({ ...x, depth: d }); list.filter((y) => x.i.label && y.i.parent === x.i.label).forEach((y) => walk(y, d + 1)) }
  list.filter((x) => !x.i.parent || x.i.parent.includes(':') || !byName(x.i.parent)).forEach((x) => walk(x, 0))
  list.forEach((x) => walk(x, 0))
  return out
})
// physical ports first (flat), then what sits on them: bonds, bridges, VLANs, tunnels, as a tree
const ifGroups = computed(() => {
  const rows = ifRows.value
  const phys = rows.filter((r) => isPhysical(r.i)).map((r) => ({ ...r, depth: 0 }))
  const virt = rows.filter((r) => !isPhysical(r.i))
  const virtNames = new Set(virt.map((r) => r.i.label).filter(Boolean))
  const out = []
  const walk = (r, d) => { if (out.some((o) => o.idx === r.idx)) return; out.push({ ...r, depth: d }); virt.filter((y) => r.i.label && y.i.parent === r.i.label).forEach((y) => walk(y, d + 1)) }
  virt.filter((r) => !virtNames.has(r.i.parent)).forEach((r) => walk(r, 0))
  virt.forEach((r) => walk(r, 0))
  return [{ id: 'physical', label: 'Physical', rows: phys }, { id: 'virtual', label: 'Virtual', rows: out }]
})
// several interfaces at once from a pattern: eth[0-3], 1/0/[1-48]
const bulk = ref(null)
const bulkNames = computed(() => (bulk.value ? expandRange(bulk.value.pattern) : []))
const bulkNew = computed(() => bulkNames.value.filter((n) => !form.interfaces.some((i) => i.label === n)))
function openBulk() { bulk.value = { pattern: '', kind: 'ethernet', connector: 'rj45', speed: '1G', network: '', parent: '', panel: false, rear: 'r' } }
function saveBulk() {
  const b = bulk.value
  for (const label of bulkNew.value) {
    const panel = b.panel && isPhysical(b) && !form.interfaces.some((i) => i.label === `${b.rear}${label}`)
    form.interfaces.push({ ...blankIf(), primary: false, label, kind: b.kind, connector: isPhysical(b) ? b.connector : '', speed: isPhysical(b) ? b.speed : '',
      network: b.network, parent: isPhysical(b) ? '' : b.parent, passthrough: panel ? `${b.rear}${label}` : '' })
    // a patch panel: each front port has a rear port it passes straight through to
    if (panel) form.interfaces.push({ ...blankIf(), primary: false, label: `${b.rear}${label}`, kind: b.kind, connector: b.connector, speed: b.speed, passthrough: label })
  }
  bulk.value = null
}
const ifIcon = (i) => ({ wifi: Wifi, bond: Merge, bridge: Waypoints, vlan: Tag, virtual: Box, wireguard: Lock, loopback: RotateCcw }[i.kind] || EthernetPort)
const cabDlg = ref(null)
function openCable(idx) {
  cabDlg.value = { idx, c: idx < 0 ? { port: form.interfaces.find((x) => x.label && !form.cables.some((c) => c.port === x.label))?.label || '', peer: '', peer_port: '', medium: 'copper', speed: '', notes: '', label: '', length: '', colour: '' } : clone(form.cables[idx]) }
}
// a port on the front panel: its cable, or a new cable from it
function pickPort(i) {
  const n = form.cables.findIndex((c) => c.port === i.label && !isInternal(c))
  if (n >= 0) openCable(n)
  else { openCable(-1); cabDlg.value.c.port = i.label; cabDlg.value.c.speed = i.speed || '' }
}
function saveCable() { const { idx, c } = cabDlg.value; if (idx < 0) form.cables.push(c); else form.cables[idx] = c; cabDlg.value = null }
// internal cables join a guest and the host it runs on (or two guests of one host): offer only those
const hostRel = useQuery('/api/hosts?fields=name,fun,runs_on')
const internalPeers = computed(() => {
  const me = form.name, on = form.runs_on
  return (hostRel.data.value || []).filter((h) => h.name !== me && (h.name === on || h.runs_on === me || (on && h.runs_on === on)))
    .map((h) => ({ name: h.name, why: h.name === on ? 'runs this host' : h.runs_on === me ? 'runs on this host' : `also on ${on}` }))
})
const isInternal = (c) => c.medium === 'virtual' || c.medium === 'internal'
function setCableType(internal) {
  const c = cabDlg.value.c
  c.medium = internal ? 'virtual' : 'copper'
  if (internal && !internalPeers.value.some((p) => p.name === c.peer)) c.peer = internalPeers.value[0]?.name || ''
}
const peerPorts = ref([])
watch(() => cabDlg.value?.c.peer, async (peer) => {
  peerPorts.value = []
  if (!peer) return
  if (peer === form.name) { peerPorts.value = form.interfaces.filter((i) => i.label).map((i) => ({ label: i.label, kind: i.kind || '' })); return }
  try {
    const h = await getCached(`/api/hosts/${encodeURIComponent(peer)}`, 30000)
    peerPorts.value = h.interfaces.filter((i) => i.label).map((i) => ({ label: i.label, kind: i.kind || '' }))
    const c = cabDlg.value?.c
    if (c && isInternal(c) && !c.peer_port) c.peer_port = (peerPorts.value.find((i) => i.kind === 'bridge') || peerPorts.value[0])?.label || ''
  } catch { /* the peer's ports are only a suggestion */ }
})
const svcDlg = ref(null)
function openSvc(idx) {
  svcDlg.value = { idx, s: idx < 0 ? { name: '', port: '', proto: 'tcp', scheme: '', path: '', dns_name: '', publish_srv: false, description: '', bind: '', link_to: '', public: false, public_port: '', public_name: '', allow_from: [] } : clone(form.services[idx]) }
}
function saveSvc() { const { idx, s } = svcDlg.value; if (idx < 0) form.services.push(s); else form.services[idx] = s; svcDlg.value = null }
const svcLink = (sv) => {
  if (!sv.scheme || !['http', 'https'].includes(sv.scheme)) return ''
  const host = sv.dns_name ? (sv.dns_name.endsWith('.') ? sv.dns_name.slice(0, -1) : `${sv.dns_name}.${hostZone.value}`) : `${form.name || 'host'}.…`
  return `${sv.scheme}://${host}${sv.path || ''}`
}
const ruleDlg = ref(null)
function openRule(idx, direction = 'in') { ruleDlg.value = { idx, r: idx < 0 ? { direction, peer: '', ports: [], description: '' } : clone(form.rules[idx]) } }
function saveRule() { const { idx, r } = ruleDlg.value; if (idx < 0) form.rules.push(r); else form.rules[idx] = r; ruleDlg.value = null }
const cableAt = (port) => form.cables.find((c) => c.port === port)
// another physical port of this host to pass through to (a patch panel's front and rear)
const throughChoices = (i) => form.interfaces.filter((x) => x !== i && x.label && isPhysical(x) && (!x.passthrough || x.passthrough === i.label)).map((x) => x.label)
function setThrough(i, v) {
  const old = form.interfaces.find((x) => x.label && x.label === i.passthrough)
  if (old && old.passthrough === i.label) old.passthrough = ''
  i.passthrough = v
  const now = form.interfaces.find((x) => x.label === v)
  if (now) now.passthrough = i.label
}
const traceOf = ref(null) // {port}: the trace dialog
const tagList = ref([])
api.get('/api/tags').then((t) => { tagList.value = t.map((x) => ({ value: x.name, label: x.description || (x.hosts ? `${x.hosts} host${x.hosts === 1 ? '' : 's'}` : '') })) }).catch(() => {})
const errors = ref({})
const busy = ref(false)
const notFound = ref(false)
// an existing host opens on its dashboard; a new one on the form
const firstTab = () => (route.params.name === 'new' ? 'overview' : 'dashboard')
const tab = ref(String(route.query.tab || firstTab()))
function setTab(t) {
  tab.value = t
  router.replace({ query: { ...route.query, tab: t === firstTab() ? undefined : t } })
}
const errIn = (prefix) => Object.keys(errors.value).some((k) => k.startsWith(prefix))
const tabs = computed(() => [
  ...(isNew.value ? [] : [{ id: 'dashboard', label: 'Dashboard' }]),
  { id: 'overview', label: 'Overview', err: ['name', 'fun', 'zone', 'status', 'function', 'runs_on', 'hardware'].some((k) => errors.value[k]) },
  { id: 'network', label: 'Network', count: form.interfaces.length, err: errIn('interfaces') },
  { id: 'services', label: 'Services', count: form.services.length, err: errIn('services') },
  { id: 'dns', label: 'DNS', count: form.records.length, err: errIn('records') || !!errors.value.fun_no_dns },
  { id: 'ansible', label: 'Ansible', count: form.features.length, err: !!(errors.value.features || errors.value.vars) },
  ...(isNew.value ? [] : [{ id: 'integrations', label: 'Plugins' }, { id: 'secrets', label: 'Secrets' }, { id: 'runs', label: 'Runs' }, { id: 'history', label: 'History' }]),
])
// <category>-<type>-<role>-<nn> from the lists under Naming
const parts = ref({ category: [], type: [], role: [] })
const nb = reactive({ category: '', type: '', role: '', nn: '', custom: false })
const funInput = ref('')
function splitName(n) {
  const m = /^([a-z0-9]+)-([a-z0-9]+)-([a-z0-9]+)-(\d{2,3})$/.exec(n || '')
  return m ? { category: m[1], type: m[2], role: m[3], nn: m[4] } : null
}
function syncBuilder() {
  const s = splitName(form.name)
  if (s) Object.assign(nb, s, { custom: false })
  else nb.custom = !!form.name
}
let nbTimer
async function buildName(keepNn = false) {
  if (nb.custom || !nb.category || !nb.type || !nb.role) return
  const same = splitName(form.name)
  if (keepNn && same && same.category === nb.category && same.type === nb.type && same.role === nb.role) return
  clearTimeout(nbTimer)
  nbTimer = setTimeout(async () => {
    const q = new URLSearchParams({ category: nb.category, type: nb.type, role: nb.role })
    if (!isNew.value) q.set('exclude', derived.value?.name || props.name)
    const r = await api.get(`/api/naming/next?${q}`).catch(() => null)
    if (r) { form.name = r.name; nb.nn = String(r.nn).padStart(2, '0') }
  }, 120)
}
function setNn(v) {
  nb.nn = v
  if (nb.category && nb.type && nb.role && /^\d{1,3}$/.test(v)) form.name = `${nb.category}-${nb.type}-${nb.role}-${String(Number(v)).padStart(2, '0')}`
}
// Fun name suggestions from the pools (Naming → Fun name pools)
const sugg = ref(null) // { names: [{name, pool}], pools, matched }
const suggPool = ref('')
const poolList = ref([])
async function suggest() {
  const q = new URLSearchParams({ category: nb.category || form.name.split('-')[0] || '', zone: form.zone || '', count: '10' })
  if (suggPool.value) q.set('pool', suggPool.value)
  sugg.value = await api.get(`/api/naming/fun-suggest?${q}`).catch((e) => { toast(e.message, 'error'); return null })
  if (!poolList.value.length) poolList.value = (await api.get('/api/naming/pools').catch(() => [])).filter((p) => p.enabled)
}
function takeFun(n) {
  if (!form.fun_names.includes(n)) form.fun_names.push(n)
  sugg.value.names = sugg.value.names.filter((x) => x.name !== n)
}
function toggleFunDns(f) {
  const i = form.fun_no_dns.indexOf(f)
  if (i >= 0) form.fun_no_dns.splice(i, 1)
  else form.fun_no_dns.push(f)
}
function addFun() {
  const v = funInput.value.trim().toLowerCase()
  if (v && !form.fun_names.includes(v)) form.fun_names.push(v)
  funInput.value = ''
}
const netByName = (n) => networks.value.find((x) => x.name === n)
const ifaceDns = (i) => (i.in_dns == null ? !!netByName(i.network)?.in_dns : !!i.in_dns)
function setIfaceDns(i, v) { i.in_dns = v === !!netByName(i.network)?.in_dns ? null : v }
const featureList = ref([])
const allNets = ref(false)
const addFeature = ref('')
// Home zone: the primary interface's network (else the first interface's), unless pinned.
const homeNet = computed(() => {
  const withNet = form.interfaces.filter((i) => i.network)
  return netByName((withNet.find((i) => i.primary) || withNet[0])?.network)
})
const derivedZone = computed(() => homeNet.value?.zone || '')
watch(() => form.runs_on, async (hv) => {
  hypervisorIfaces.value = []
  if (!hv) return
  try { hypervisorIfaces.value = ((await getCached(`/api/hosts/${encodeURIComponent(hv)}`, 60000)).interfaces || []).filter((x) => x.label) } catch { /* gone */ }
}, { immediate: true })
watch(derivedZone, (z) => { if (!form.zone_pinned && z) form.zone = z }, { immediate: true })
function setZone(value) {
  form.zone_pinned = true
  form.zone = value
}
function followZone() { form.zone_pinned = false; if (derivedZone.value) form.zone = derivedZone.value }
const zoneOf = (i) => netByName(i.network)?.zone || ''
// Zone first, then segment: offer networks of the host's zone (plus whatever is already chosen).
// Without a zone yet, every network: the first one picked gives the zone.
const netsFor = (iface) => networks.value.filter((n) => allNets.value || !form.zone || n.zone === form.zone || n.name === iface.network)
const otherZoneNets = computed(() => form.zone ? networks.value.filter((n) => n.zone !== form.zone).length : 0)
// VIPs
const isVip = computed(() => form.kind === 'vip')
watch(() => form.kind, (k) => {
  if (k === 'vip' && isNew.value && !nb.custom && parts.value.type.some((p) => p.code === 'vip')) { nb.type = 'vip'; buildName() }
})
function addMember(name) { if (name && !form.vip_members.includes(name) && name !== form.name) form.vip_members.push(name) }

const readOnly = computed(() => !isNew.value && derived.value?.can_edit === false)
const dirty = computed(() => !!saved.value && JSON.stringify(payload()) !== saved.value) // nothing to lose before it loaded
const startedAs = ref('') // a new host: what the form looked like once loaded (and prefilled)
const edited = computed(() => (isNew.value ? startedAs.value !== '' && JSON.stringify(payload()) !== startedAs.value : dirty.value))
const unsaved = useUnsaved(edited, () => save())
const draft = useDraft(() => `host:${isNew.value ? 'new' : props.name}`, () => form, (d) => { Object.assign(form, d); syncBuilder() }, edited,
  computed(() => !!saved.value || startedAs.value !== ''))

function payload(f = form) {
  return {
    ...f,
    tags: [...f.tags],
    features: [...f.features],
    fun_names: [...f.fun_names],
    fun_no_dns: (f.fun_no_dns || []).filter((x) => f.fun_names.includes(x)),
    records: (f.records || []).map((r) => ({ name: r.name || '', type: r.type, value: r.value || '' })),
    fun: f.fun_names[0] || '',
    vip_members: f.kind === 'vip' ? [...(f.vip_members || [])] : [],
    vip_protocol: f.kind === 'vip' ? f.vip_protocol : '',
    vips: undefined,
    interfaces: f.interfaces.map((i) => ({ ...i, extra_ips: [...(i.extra_ips || [])] })),
    cables: (f.cables || []).map((c) => ({ ...c })),
    rules: (f.rules || []).map((r) => ({ ...r, ports: [...(r.ports || [])] })),
    seen: undefined, links: undefined, dns: undefined, motd: undefined, guests: undefined,
    services: f.services.map((s) => ({
      ...s, port: s.port === '' || s.port == null ? null : Number(s.port),
      allow_from: Array.isArray(s.allow_from) ? [...s.allow_from] : String(s.allow_from || '').split(/[\s,]+/).filter(Boolean),
    })),
  }
}

function fill(h) {
  Object.assign(form, blank(), {
    ...h,
    tags: [...(h.tags || [])],
    features: [...(h.features || [])],
    fun_names: [...(h.fun_names || (h.fun ? [h.fun] : []))],
    vip_members: [...(h.vip_members || [])],
    fun_no_dns: [...(h.fun_no_dns || [])],
    records: (h.records || []).map((r) => ({ ...r })),
    vars: h.vars && Object.keys(h.vars).length ? toYaml(h.vars) : '',
    interfaces: h.interfaces.map(({ ip6_all, zone, ...i }) => ({ ...i, kind: i.kind || '', parent: i.parent || '', extra_ips: [...(i.extra_ips || [])], ip6_overrides: { ...(i.ip6_overrides || {}) } })), // eslint-disable-line no-unused-vars
    cables: (h.cables || []).map((c) => ({ ...c })),
    rules: (h.rules || []).map((r) => ({ ...r, ports: [...(r.ports || [])] })),
    services: h.services.map(({ addresses, ...s }) => ({ ...s, port: s.port ?? '', allow_from: [...(s.allow_from || [])] })), // eslint-disable-line no-unused-vars
  })
  saved.value = JSON.stringify(payload())
}

async function load() {
  const [nets, hosts, feats, np] = await Promise.all([getCached('/api/networks', 60000), getCached('/api/hosts?fields=name', 60000),
    getCached('/api/features', 60000).catch(() => []), getCached('/api/naming', 60000).catch(() => null)])
  if (np) parts.value = np
  getCached('/api/racks', 60000).then((r) => { rackList.value = r.racks }).catch(() => {})
  networks.value = nets
  featureList.value = feats.map((f) => f.path)
  hostNames.value = hosts.map((h) => h.name)
  if (isNew.value) {
    // keep whatever was typed while the lists loaded
    saved.value = JSON.stringify(payload(blank()))
    // prefill from Discovery: /hosts/new?ip=…&network=…&mac=…&hint=…
    const q = route.query
    if (q.kind === 'vip') form.kind = 'vip'
    if (q.ip && !form.interfaces.length) {
      form.interfaces.push({ network: String(q.network || ''), label: '', kind: '', parent: '', extra_ips: [], ip4: String(q.ip), ip6: '', mac: String(q.mac || ''), primary: true })
      const zone = nets.find((n) => n.name === q.network)?.zone
      if (zone) form.zone = zone
      if (q.hint) form.notes = `Discovered as ${q.hint}`
      if (q.vendor) form.vendor = String(q.vendor)
    }
    await nextTick()
    startedAs.value = JSON.stringify(payload())
    return
  }
  try {
    const h = await getCached(`/api/hosts/${encodeURIComponent(props.name)}`)
    if (h.uid && props.name !== h.uid) { // a link by name: the address bar shows the stable id, which survives renames
      setQueryData(`/api/hosts/${h.uid}`, h)
      router.replace({ path: `/hosts/${h.uid}`, query: route.query })
      return
    }
    hostNames.value = hostNames.value.filter((n) => n !== h.name)
    derived.value = h
    fill(h)
    syncBuilder()
  } catch (e) {
    if (e.status === 404) notFound.value = true
    else toast(e.message, 'error')
  }
}

onMounted(load)
watch(derived, (h) => { if (h) document.title = `${h.fun || h.name} · Host · labdex` })

// Live names while typing: aliases and DNS names for the unsaved form.
let timer
watch(() => [form.name, form.zone, JSON.stringify(form.interfaces.map((i) => [i.network, i.ip6, i.ip6_token, i.mac, i.ip6_overrides]))], () => {
  clearTimeout(timer)
  timer = setTimeout(async () => {
    if (!form.name) { preview.value = null; return }
    preview.value = await api.post('/api/preview/host', payload()).catch(() => null)
  }, 200)
}, { immediate: true })

// IPv6: one interface ID per interface, used in every IPv6 prefix of the network
const pkey = (p) => (p.source === 'delegated' ? `pd:${p.delegated_from}:${p.prefix_id}` : p.prefix)
const v6Prefixes = (i) => (netByName(i.network)?.prefixes || []).filter((p) => p.family === 6)
function setOverride(i, p, v) {
  i.ip6_overrides = { ...(i.ip6_overrides || {}) }
  if (v.trim()) i.ip6_overrides[pkey(p)] = v.trim()
  else delete i.ip6_overrides[pkey(p)]
}
async function nextFree6(iface) {
  if (!iface.network) return
  const r = await api.get(`/api/networks/${iface.network}/next`).catch(() => null)
  if (r?.ip6_token) iface.ip6_token = r.ip6_token
  else toast('This network has no IPv6 prefix', 'error')
}
const RECORD_PRESETS = [
  ['Alias', { name: '', type: 'CNAME', value: '' }, 'another name for this host (CNAME to its primary record)'],
  ['A', { name: '', type: 'A', value: '{ip4}' }, 'an address record, by default the primary IPv4'],
  ['AAAA', { name: '', type: 'AAAA', value: '{ip6}' }, 'the primary IPv6'],
  ['TXT', { name: '', type: 'TXT', value: '' }, 'text, e.g. a verification token'],
  ['SRV', { name: '_svc._tcp', type: 'SRV', value: '0 5 443 {fqdn}' }, 'priority weight port target'],
  ['MX', { name: '', type: 'MX', value: '10 {fqdn}' }, 'mail for a name'],
]
function addRecord(preset) { form.records.push({ ...preset }) }
const recPlaceholder = (r) => ({ CNAME: '{target} (the primary record)', A: '{ip4} or 10.0.0.5', AAAA: '{ip6}', TXT: 'v=spf1 -all', SRV: '0 5 443 {fqdn}', MX: '10 {fqdn}' }[r.type] || '')
const domain = computed(() => state.meta.settings.domain)
const hostZone = computed(() => (form.dns_zone || '').replace(/\.$/, '') || domain.value)
const recName = (r) => {
  const n = (r.name || '').trim().toLowerCase().replaceAll('{host}', form.name || 'host').replaceAll('{fun}', form.fun_names[0] || form.name || 'host')
  return n.endsWith('.') ? n.replace(/\.$/, '') : `${n || form.name || 'host'}.${hostZone.value}`
}
async function nextFree(iface) {
  if (!iface.network) return
  const { ip } = await api.get(`/api/networks/${iface.network}/next`)
  if (ip) {
    // don't hand out an address another row of this form already claims
    const taken = new Set(form.interfaces.map((i) => i.ip4))
    if (taken.has(ip)) {
      const net = await api.get(`/api/networks/${iface.network}?free=50`)
      iface.ip4 = net.free.find((a) => !taken.has(a)) || ''
    } else {
      iface.ip4 = ip
    }
  } else {
    toast(`No free static address in ${iface.network}`, 'error')
  }
}

async function save() {
  busy.value = true
  errors.value = {}
  try {
    const body = payload()
    const h = isNew.value
      ? await api.post('/api/hosts', body)
      : await api.put(`/api/hosts/${encodeURIComponent(props.name)}`, body)
    draft.clear()
    toast(`Saved ${h.fun || h.name}`)
    await changed()
    // write-through: the answer is the full host, so the next visit needs no fetch
    setQueryData(`/api/hosts/${h.uid}`, h)
    if (isNew.value) {
      unsaved.release()
      router.replace(`/hosts/${h.uid}`)
    } else {
      derived.value = h // a rename keeps the address: it's the host's stable id
      fill(h)
    }
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) {
      errors.value = e.fields
      toast('Please fix the highlighted fields', 'error')
    } else {
      toast(e.message, 'error')
    }
  } finally {
    busy.value = false
  }
}

async function remove() {
  const name = derived.value?.name || props.name
  if (!(await confirm({ title: `Delete ${name}?`, body: `Its interfaces and services go with it.`, action: 'Delete', danger: true }))) return
  try {
    await api.del(`/api/hosts/${encodeURIComponent(props.name)}`)
    toast(`Deleted ${name}`)
    await changed()
    unsaved.release()
    router.push('/hosts')
  } catch (e) {
    toast(e.message, 'error')
  }
}

const err = (field) => errors.value[field]
const aliasPreview = computed(() => {
  if (derived.value && !dirty.value) return derived.value.aliases.map((a) => [a.name, a.length, a.fw_id])
  if (!preview.value) return []
  const has4 = form.interfaces.some((i) => i.ip4)
  const has6 = form.interfaces.some((i) => i.ip6 || i.ip6_token) || (preview.value?.ip6 || []).some((x) => x.length)
  return Object.entries(preview.value.aliases)
    .filter(([af]) => (af === '4' && has4) || (af === '6' && has6) || (af === 'dual' && (has4 || has6)))
    .map(([, v]) => v)
})
const aliasMax = computed(() => Number(state.meta.settings.alias_max_length || 32))
const nameHint = computed(() => `<category>-<type>-<role>-<nn>, categories: ${state.meta.categories.join(', ')}`)
</script>

<template>
  <div class="page">
    <div v-if="notFound" class="errbox">No host called “{{ name }}”. <RouterLink to="/hosts">Back to hosts</RouterLink></div>

    <template v-else>
      <div v-if="draft.offer.value" class="draftbar" role="status">
        <span>Changes you didn’t save from {{ stamp(new Date(draft.offer.value.at).toISOString()) }} are kept in this browser.</span>
        <span class="grow"></span>
        <button class="btn small primary" @click="draft.restore()">Restore them</button>
        <button class="btn small ghost" @click="draft.discard()">Discard</button>
      </div>
      <div class="page-head">
        <RouterLink to="/hosts" class="btn ghost small">← Hosts</RouterLink>
        <h1>
          <template v-if="isNew">New host</template>
          <template v-else>
            <span v-if="derived?.fun" class="fun" style="font-size: 22px">{{ derived.fun }}</span>
            <span class="mono" :class="{ muted: derived?.fun }" style="margin-left: 10px; font-size: 16px">{{ derived?.name || name }}</span>
          </template>
        </h1>
        <ZoneChip v-if="form.zone" :zone="form.zone" />
        <span class="grow"></span>
        <span v-if="readOnly" class="lock" title="Your roles don't allow changing this host">🔒 read-only for you</span>
        <template v-else>
          <a v-if="!isNew" class="btn" :href="`/api/hosts/${encodeURIComponent(derived?.name || name)}/label.svg`" target="_blank" rel="noopener" title="A printable label with a QR code">Label</a>
          <button v-if="!isNew" class="btn danger" @click="remove">Delete</button>
          <button class="btn primary" :disabled="busy || (!dirty && !isNew)" @click="save">
            {{ busy ? 'Saving…' : isNew ? 'Create host' : dirty ? 'Save changes' : 'Saved' }}
          </button>
        </template>
      </div>
      <nav class="tabs" aria-label="Host sections">
        <a v-for="t in tabs" :key="t.id" href="#" :class="{ 'router-link-active': tab === t.id }" :aria-current="tab === t.id ? 'page' : undefined" @click.prevent="setTab(t.id)">
          {{ t.label }}<span v-if="t.count" class="tcount">{{ t.count }}</span><span v-if="t.err" class="pill" style="margin-left: 6px">!</span>
        </a>
      </nav>

      <form @submit.prevent="save"><button type="submit" hidden></button></form>

      <HostDashboard v-if="!isNew && derived && tab === 'dashboard'" :host="derived" @tab="setTab" />
      <div v-show="tab === 'overview'" class="split">
        <div>
          <form class="card" @submit.prevent="save">
            <fieldset :disabled="readOnly" class="bare">
            <h3>Identity</h3>
            <div class="grid2">
              <div class="span2">
                <span class="flabel">Hostname</span>
                <div v-if="!nb.custom" class="builder">
                  <select v-model="nb.category" aria-label="Category" @change="buildName()">
                    <option value="" disabled>category</option>
                    <option v-for="p in parts.category" :key="p.code" :value="p.code">{{ p.code }} · {{ p.label }}</option>
                  </select>
                  <span class="dash">-</span>
                  <select v-model="nb.type" aria-label="Type" @change="buildName()">
                    <option value="" disabled>type</option>
                    <option v-for="p in parts.type" :key="p.code" :value="p.code">{{ p.code }} · {{ p.label }}</option>
                  </select>
                  <span class="dash">-</span>
                  <input v-model.trim="nb.role" class="mono role-in" list="role-suggestions" aria-label="Role" placeholder="role"
                         spellcheck="false" autocomplete="off" @input="nb.role = nb.role.toLowerCase().replace(/[^a-z0-9]/g, ''); buildName()">
                  <datalist id="role-suggestions">
                    <option v-for="p in parts.role" :key="p.code" :value="p.code">{{ p.label }}</option>
                    <option v-for="r in parts.roles_in_use || []" :key="'u' + r" :value="r">in use</option>
                  </datalist>
                  <span class="dash">-</span>
                  <input :value="nb.nn" class="mono nn" aria-label="Number" placeholder="nn" @input="setNn($event.target.value)">
                  <a href="#" class="small" @click.prevent="nb.custom = true">type it</a>
                </div>
                <div v-else class="row" style="flex-wrap: nowrap">
                  <input v-model.trim="form.name" type="text" class="mono" :class="{ invalid: err('name') }"
                         placeholder="srv-vm-dns-01" autocomplete="off" spellcheck="false">
                  <a href="#" class="small nowrap" @click.prevent="nb.custom = false; syncBuilder(); buildName(true)">use the scheme</a>
                </div>
                <div v-if="err('name')" class="err">{{ err('name') }}</div>
                <div v-else class="hint">
                  <span class="mono" style="color: var(--ink)">{{ form.name || '…' }}</span>
                  <template v-if="!nb.custom"> · the next free number is filled in<template v-if="state.meta.is_admin">; lists under <RouterLink to="/naming">Naming</RouterLink></template></template>
                  <template v-else-if="preview && !preview.name_ok"> · doesn't follow {{ nameHint }}</template>
                </div>
              </div>
              <div class="span2">
                <span class="flabel">Fun names</span>
                <div class="row" style="gap: 6px">
                  <span v-for="(f, n) in form.fun_names" :key="f" class="chip" :class="{ on: n === 0 }" :title="n === 0 ? 'Primary: prompt and banner' : 'Click to make primary'"
                        @click="n && form.fun_names.unshift(form.fun_names.splice(n, 1)[0])">{{ f }}
                    <button type="button" class="chipx" aria-label="Remove" @click.prevent.stop="form.fun_names.splice(n, 1)">✕</button></span>
                  <input v-model="funInput" type="text" placeholder="+ fun name" style="width: 150px" spellcheck="false"
                         @keydown.enter.prevent="addFun" @keydown.,.prevent="addFun" @blur="addFun">
                  <button type="button" class="btn small" @click="suggest">Suggest</button>
                </div>
                <div v-if="sugg" class="sugg">
                  <span class="small faint">{{ sugg.matched ? 'from' : 'no pool for this category or zone; from' }} {{ sugg.pools.join(', ') || 'no pools' }}:</span>
                  <button v-for="n in sugg.names" :key="n.name" type="button" class="chip" :title="`from ${n.pool}`" @click="takeFun(n.name)">{{ n.name }}</button>
                  <span v-if="!sugg.names.length" class="small faint">nothing unused left</span>
                  <select v-model="suggPool" class="small auto" aria-label="Pool" @change="suggest">
                    <option value="">matching pools</option>
                    <option v-for="p in poolList" :key="p.name" :value="p.name">{{ p.name }}</option>
                  </select>
                  <button type="button" class="btn small ghost" title="Other names" @click="suggest">↻</button>
                  <button type="button" class="btn small ghost" aria-label="Close suggestions" @click="sugg = null">✕</button>
                </div>
                <div v-if="err('fun')" class="err">{{ err('fun') }}</div>
                <div v-else class="hint">Memorable names; each becomes a DNS CNAME. The first is the primary one (prompt, banner).</div>
              </div>
              <div class="span2 zonebox">
                <span class="flabel">Home zone</span>
                <div class="seg" role="radiogroup" aria-label="Home zone">
                  <button type="button" role="radio" :aria-checked="!form.zone_pinned" :class="{ on: !form.zone_pinned }" :disabled="readOnly" @click="followZone">Follow the primary interface</button>
                  <button type="button" role="radio" :aria-checked="!!form.zone_pinned" :class="{ on: form.zone_pinned }" :disabled="readOnly" @click="form.zone_pinned = true">Choose</button>
                </div>
                <div class="zsel">
                  <select :value="form.zone" aria-label="Zone" :disabled="!form.zone_pinned" :class="{ invalid: err('zone') }"
                          @change="setZone($event.target.value)">
                    <option value="" disabled>zone</option>
                    <optgroup v-for="a in state.meta.areas" :key="a.code" :label="`${a.code} · ${a.name}`">
                      <option v-for="z in state.meta.zones.filter((x) => x.area === a.code)" :key="z.code" :value="z.code">{{ z.code }} · {{ z.long }}</option>
                    </optgroup>
                  </select>
                  <ZoneChip v-if="form.zone && state.meta.zones.some((z) => z.code === form.zone)" :zone="form.zone" />
                </div>
                <div v-if="err('zone')" class="err">{{ err('zone') }}</div>
                <div v-else class="hint">
                  <template v-if="!form.zone_pinned && derivedZone">From {{ homeNet.name }}: {{ state.meta.zones.find((z) => z.code === derivedZone)?.long }}.</template>
                  <template v-else-if="!form.zone_pinned">Add an interface on the Network tab, or choose a zone.</template>
                  <template v-else>Kept even if the primary interface moves<template v-if="derivedZone && derivedZone !== form.zone"> (its network is in {{ derivedZone }})</template>.</template>
                  The home zone names the host's alias and decides who may edit it; each address keeps its own network's zone.
                </div>
              </div>
              <Field label="Kind" :error="err('kind')">
                <select v-model="form.kind">
                  <option value="device">Device</option>
                  <option value="vip">VIP (shared address)</option>
                </select>
              </Field>
              <Field v-if="isVip" label="Protocol" :error="err('vip_protocol')" hint="How the members share the address">
                <select v-model="form.vip_protocol">
                  <option value="">—</option>
                  <option v-for="p in state.meta.vip_protocols" :key="p" :value="p">{{ p }}</option>
                </select>
              </Field>
              <div v-if="isVip" class="span2">
                <span class="flabel">Members</span>
                <div class="row" style="gap: 6px">
                  <span v-for="(m, n) in form.vip_members" :key="m" class="chip"><RouterLink :to="`/hosts/${m}`" class="mono">{{ m }}</RouterLink>
                    <button type="button" class="chipx" aria-label="Remove member" @click.prevent="form.vip_members.splice(n, 1)">✕</button></span>
                  <HostPicker placeholder="+ member" :zone="form.zone" @pick="addMember" />
                </div>
                <div v-if="err('vip_members')" class="err">{{ err('vip_members') }}</div>
                <div v-else class="hint">The hosts that hold this address. They list it on their Network tab and get it as <code>labdex_vips</code> in Ansible; the VIP itself is never an Ansible target.</div>
              </div>
              <Field label="Status" :error="err('status')">
                <select v-model="form.status">
                  <option v-for="s in state.meta.statuses" :key="s" :value="s">{{ s }}</option>
                </select>
              </Field>
              <Field label="Function" :error="err('function')" hint="What it does, in words">
                <input v-model="form.function" type="text" placeholder="DNS resolver">
              </Field>
              <Field v-if="!isVip" label="Runs on" :error="err('runs_on')" hint="The hypervisor for a VM or container">
                <select v-model="form.runs_on">
                  <option value="">— physical / external —</option>
                  <option v-for="n in hostNames" :key="n" :value="n">{{ n }}</option>
                </select>
              </Field>
              <Field v-if="!isVip" label="Hardware" :error="err('hardware')">
                <input v-model="form.hardware" type="text" placeholder="Supermicro X11SSM-F">
              </Field>
              <Field v-if="!isVip" label="Size" :error="err('rack_height')" hint="Height in rack units (U, HE); used when it's put in a rack">
                <div class="inline" style="align-items: center"><input v-model.number="form.rack_height" type="number" min="1" max="60" class="mono" style="width: 90px" aria-label="Size in rack units"><span class="faint">U</span></div>
              </Field>
              <Field v-if="!isVip" label="Rack" :error="err('rack') || err('rack_unit') || err('rack_face')"
                     :hint="form.rack ? 'Unit: the lowest one it takes, 1 at the bottom' : 'Racks are made on the Racks page'">
                <div class="rackrow">
                  <select v-model="form.rack" aria-label="Rack"><option value="">— not in a rack —</option>
                    <option v-for="r in rackList" :key="r.uid" :value="r.name">{{ r.name }}{{ r.site ? ` (${r.site})` : '' }}</option></select>
                  <template v-if="form.rack">
                    <input :value="form.rack_unit ?? ''" type="number" min="1" placeholder="U" aria-label="Unit" class="mono"
                           @input="form.rack_unit = $event.target.value === '' ? null : Number($event.target.value)">
                    <select v-model="form.rack_face" aria-label="Side"><option value="front">front</option><option value="rear">rear</option><option value="full">full depth</option></select>
                  </template>
                </div>
              </Field>
              <Field v-if="!isVip" label="Vendor" hint="Filled from Discovery (the MAC's vendor) when empty">
                <input v-model="form.vendor" type="text" placeholder="Ubiquiti">
              </Field>
              <Field v-if="!isVip" label="Model">
                <input v-model="form.model" type="text" placeholder="USW-Pro-24">
              </Field>
              <Field label="Icon" hint="Empty: from the name's type (vm, pm, fw, sw, …)">
                <div class="iconpick">
                  <button type="button" class="ip" :class="{ on: !form.icon }" title="automatic" @click="form.icon = ''"><HostIcon :host="{ ...form, icon: '' }" /></button>
                  <button v-for="(v, k) in ICONS" :key="k" type="button" class="ip" :class="{ on: form.icon === k }" :title="k" @click="form.icon = k"><HostIcon :icon="k" /></button>
                </div>
              </Field>
              <Field label="Colour on the map" :error="err('colour')" hint="Empty: the zone's colour">
                <ColourPicker v-model="form.colour" :none-colour="zoneHex(form.zone)" none-title="the zone's colour" /></Field>
              <Field label="Tags" :error="err('tags')" hint="Pick or type; new ones are added to the tag list. Each becomes an Ansible group tag_<name>.">
                <ChipInput v-model="form.tags" :suggestions="tagList" :colour="tagColour" placeholder="admin, backup" label="Add tag"
                           :normalize="(v) => v.trim().toLowerCase().replace(/\s+/g, '-')" />
              </Field>
            </div>
            <div style="margin-top: 12px">
              <Field label="Notes">
                <textarea v-model="form.notes" rows="2" style="font-family: inherit; font-size: 14px"></textarea>
              </Field>
            </div>
            <button type="submit" hidden></button>
            </fieldset>
          </form>
          <div class="lower1">
          <div v-if="derived?.motd" class="card">
            <h3>Login banner</h3>
            <pre class="code">{{ derived.motd.trim() }}</pre>
            <p class="hint">Rendered by Ansible from the inventory (labdex_motd, labdex_ps1).</p>
          </div>
            <div v-if="derived?.issues?.length" class="card">
            <h3>Checks</h3>
            <ul class="plain">
              <li v-for="i in derived.issues" :key="i.code + i.message">
                <span class="sev" :class="`sev-${i.severity}`">{{ i.severity }}</span> {{ i.message }}
              </li>
            </ul>
          </div>
          <div v-if="derived?.guests?.length" class="card">
            <h3>Runs here</h3>
            <ul class="plain">
              <li v-for="g in derived.guests" :key="g"><RouterLink :to="`/hosts/${g}`" class="mono">{{ g }}</RouterLink></li>
            </ul>
          </div>
          </div>
        </div>
        <aside>
          <div class="card">
            <h3>Firewall aliases</h3>
            <ul v-if="aliasPreview.length" class="plain">
              <li v-for="v in aliasPreview" :key="v[0]">
                <Copy :text="v[0]" />
                <span class="small faint" style="margin-left: 8px">{{ v[1] }}/{{ aliasMax }}</span>
                <div v-if="v[2] && v[2] !== v[0]" class="small faint">on the firewall: <span class="mono">{{ v[2] }}</span></div>
              </li>
            </ul>
            <p v-else class="faint" style="margin: 0">{{ form.name ? 'Add an address to get aliases.' : 'Enter a hostname to see them.' }}</p>
            <p class="hint">H4/H6 hold the addresses; H_ nests both. Names longer than the firewall allows get a shortened id; the full name goes into the description.</p>
          </div>
          <div v-if="derived?.dns?.length && !dirty" class="card">
            <h3>DNS records</h3>
            <ul class="plain">
              <li v-for="r in derived.dns" :key="r.fqdn + r.type + r.value">
                <Copy :text="r.fqdn" /> <span class="faint small">{{ r.type }} {{ r.type === 'CNAME' || r.type === 'SRV' ? '' : r.value }}</span>
              </li>
            </ul>
          </div>
          <div v-else-if="preview?.dns?.length" class="card">
            <h3>DNS names</h3>
            <ul class="plain"><li v-for="d in preview.dns" :key="d" class="mono">{{ d }}</li></ul>
            <p class="hint">Save to see every record, including CNAMEs and SRV.</p>
          </div>
        </aside>
      </div>

      <fieldset v-show="tab === 'network'" class="bare" :disabled="readOnly">
        <section class="tsec">
          <div class="sect-head">
            <h2>Interfaces</h2>
            <span v-if="form.interfaces.length" class="note">{{ form.interfaces.length }}</span>
            <span class="grow"></span>
            <button class="btn small" @click="openBulk"><ListPlus :size="14" />Several</button>
            <button class="btn small" @click="openIf(-1)"><Plus :size="14" />Interface</button>
          </div>
          <div v-if="err('interfaces')" class="err" style="margin-bottom: 8px">{{ err('interfaces') }}</div>
          <PortMap :interfaces="form.interfaces" :cables="form.cables" @pick="pickPort" />
          <template v-if="form.interfaces.length">
          <template v-for="g in ifGroups" :key="g.id">
          <div v-if="g.rows.length" class="ifgroup small muted">{{ g.label }} <span class="faint">{{ g.rows.length }}</span>
            <span v-if="g.id === 'virtual'" class="faint">&nbsp;· sits on a port, a bond or bridge, or on nothing (loopback)</span></div>
          <div v-if="g.rows.length" class="list">
            <div v-for="r in g.rows" :key="r.idx" class="li" :class="{ nested: r.depth === 1, nested2: r.depth > 1, bad: errIn(`interfaces[${r.idx}]`) }">
              <component :is="ifIcon(r.i)" :size="17" class="li-ic" />
              <div class="li-main">
                <div class="li-title">
                  <span class="mono">{{ r.i.label || r.i.network || `interface ${r.idx + 1}` }}</span>
                  <span v-if="r.i.kind" class="faint small">{{ r.i.kind }}</span>
                  <span v-if="r.i.passthrough" class="small muted" :title="`Passes through to ${r.i.passthrough} (a patch panel's front and rear)`"><ArrowLeftRight :size="12" /> <span class="mono">{{ r.i.passthrough }}</span></span>
                  <span v-if="r.i.connector || r.i.speed" class="small"><i v-if="speedStep(r.i.speed)" class="spd" :style="{ background: speedStep(r.i.speed).colour }"></i>{{ [r.i.connector, r.i.speed].filter(Boolean).join(' · ') }}</span>
                  <span v-if="g.id === 'virtual' && r.depth === 0 && r.i.parent && !r.i.parent.includes(':')" class="small muted">on <span class="mono">{{ r.i.parent }}</span></span>
                  <ZoneChip v-if="zoneOf(r.i)" :zone="zoneOf(r.i)" />
                  <span v-if="r.i.label && r.i.network" class="small muted">{{ r.i.network }}</span>
                  <span v-if="r.i.primary" class="tag">primary</span>
                  <span v-if="r.i.network && !ifaceDns(r.i)" class="tag" title="No DNS records for this interface">no DNS</span>
                  <span v-if="errIn(`interfaces[${r.idx}]`)" class="small c-red">has errors</span>
                </div>
                <div class="li-sub">
                  <span v-if="r.i.ip4"><Copy :text="r.i.ip4" /></span>
                  <span v-for="a in (preview?.ip6?.[r.idx] || [])" :key="a.address" class="mono">{{ a.address }}</span>
                  <span v-for="a in r.i.extra_ips || []" :key="a" class="mono" title="Secondary address">+ {{ a }}</span>
                  <span v-if="r.i.mac" class="mono faint">{{ r.i.mac }}</span>
                  <span v-if="r.i.parent && r.i.parent.includes(':')">on <RouterLink :to="`/hosts/${r.i.parent.split(':')[0]}?tab=network`" class="mono">{{ r.i.parent }}</RouterLink></span>
                  <span v-if="cableAt(r.i.label)" class="cab"><Cable :size="13" /> <RouterLink :to="`/hosts/${cableAt(r.i.label).peer}?tab=network`">{{ cableAt(r.i.label).peer }}</RouterLink>
                    <span class="mono">{{ cableAt(r.i.label).peer_port }}</span></span>
                  <RouterLink v-if="derived?.switch_ports?.[r.i.label || r.i.mac] && !cableAt(r.i.label)" class="seen"
                              :to="`/discovery?tab=where&q=${encodeURIComponent(r.i.mac)}`" :title="`From the switch's MAC table (${derived.switch_ports[r.i.label || r.i.mac].source}), ${ago(derived.switch_ports[r.i.label || r.i.mac].seen_at)}`">
                    <MapPin :size="12" /> on {{ derived.switch_ports[r.i.label || r.i.mac].switch }}&nbsp;<span class="mono">{{ derived.switch_ports[r.i.label || r.i.mac].port }}</span></RouterLink>
                  <template v-for="ip in ifaceIps(r.i)" :key="'s' + ip">
                    <span v-if="derived?.seen?.[ip]" class="seen" :title="`Discovery: ${derived.seen[ip].source} ${ago(derived.seen[ip].last_seen)}`">
                      <span class="dot" :class="derived.seen[ip].up ? 'up' : ''"></span>seen{{ derived.seen[ip].hostname ? ` as ${derived.seen[ip].hostname}` : '' }}{{ derived.seen[ip].vendor ? `, ${derived.seen[ip].vendor}` : '' }}</span>
                  </template>
                </div>
              </div>
              <div class="li-actions">
                <button v-if="r.i.label && (cableAt(r.i.label) || r.i.passthrough) && !isNew" class="btn icon" :aria-label="`Trace ${r.i.label}`" title="Trace the cable end to end"
                        @click="traceOf = { port: r.i.label }"><Route :size="15" /></button>
                <button class="btn icon" :aria-label="`Edit ${r.i.label || r.i.network}`" @click="openIf(r.idx)"><Pencil :size="15" /></button>
                <button class="btn icon" :aria-label="`Remove ${r.i.label || r.i.network}`" @click="removeIf(r.idx)"><Trash2 :size="15" /></button>
              </div>
            </div>
          </div>
          </template>
          </template>
          <div v-if="!form.interfaces.length" class="list"><div class="list-empty">No interfaces yet. Add one to give the host addresses, DNS names and firewall aliases.</div></div>
        </section>

        <section v-if="!isVip" class="tsec">
          <div class="sect-head">
            <h2>Cables</h2><span class="note">which port goes where, and internal links to the hypervisor</span><span class="grow"></span>
            <button class="btn small" @click="openCable(-1)"><Plus :size="14" />Cable</button>
          </div>
          <div class="list">
            <div v-for="(c, n) in form.cables" :key="n" class="li" :class="{ bad: errIn(`cables[${n}]`) }">
              <component :is="isInternal(c) ? Waypoints : Cable" :size="17" class="li-ic" />
              <div class="li-main">
                <div class="li-title"><span class="mono">{{ c.port || '?' }}</span><span class="faint">{{ isInternal(c) ? 'inside, on' : 'to' }}</span>
                  <RouterLink v-if="c.peer" :to="`/hosts/${c.peer}?tab=network`">{{ c.peer }}</RouterLink>
                  <span class="mono">{{ c.peer_port }}</span>
                  <span v-if="errIn(`cables[${n}]`)" class="small c-red">has errors</span></div>
                <div v-if="c.medium || c.speed || c.notes || c.label || c.length" class="li-sub"><span v-if="c.label" class="mono">{{ c.label }}</span><span v-if="c.length">{{ c.length }} m</span><span><i v-if="speedStep(c.speed) && !isInternal(c)" class="spd" :style="{ background: speedStep(c.speed).colour }"></i>{{ [isInternal(c) ? 'internal' : c.medium, c.speed].filter(Boolean).join(', ') }}</span><span v-if="c.notes">{{ c.notes }}</span></div>
              </div>
              <div class="li-actions">
                <button class="btn icon" aria-label="Edit cable" @click="openCable(n)"><Pencil :size="15" /></button>
                <button class="btn icon" aria-label="Remove cable" @click="form.cables.splice(n, 1)"><Trash2 :size="15" /></button>
              </div>
            </div>
            <div v-if="!form.cables.length" class="list-empty">No cables. A cable is stored once and shows on both hosts; the far end can be a switch port labdex doesn't know as an interface.</div>
          </div>
        </section>
        <section v-if="derived?.vips?.length" class="tsec">
          <div class="sect-head">
            <h2>Shared addresses (VIPs)</h2><span class="note">VIPs this host is a member of; change members on the VIP itself</span>
          </div>
          <div class="list">
            <div v-for="v in derived.vips" :key="v.name" class="li">
              <Waypoints :size="17" class="li-ic" />
              <div class="li-main">
                <div class="li-title"><RouterLink :to="`/hosts/${v.name}`" class="mono">{{ v.name }}</RouterLink><span v-if="v.protocol" class="faint">{{ v.protocol }}</span></div>
                <div class="li-sub"><span v-for="(a, n) in v.addresses" :key="n"><span class="faint">{{ a.network }}</span> <Copy :text="a.ip4 || a.ip6" /></span></div>
              </div>
            </div>
          </div>
        </section>
        <p v-if="isVip" class="hint">A VIP's interface holds the shared address only: no MAC, no DHCP reservation.</p>
      </fieldset>

      <fieldset v-show="tab === 'dns'" class="bare" :disabled="readOnly">
        <div class="card">
          <h3>Zone</h3>
          <Field label="Main DNS zone" :error="err('dns_zone')"
                 :hint="`Where the short name, fun names, service names and extra records go. Empty: ${domain}. Address records follow their network's zone.`">
            <input v-model.trim="form.dns_zone" type="text" class="mono" :placeholder="domain" spellcheck="false" style="max-width: 360px">
          </Field>
        </div>
        <div class="card">
          <h3>Fun names</h3>
          <ul v-if="form.fun_names.length" class="plain">
            <li v-for="f in form.fun_names" :key="f">
              <label class="check"><input type="checkbox" :checked="!form.fun_no_dns.includes(f)" @change="toggleFunDns(f)">
                <span class="fundns"><span class="mono">{{ f }}.{{ hostZone }}</span> <span class="faint small">CNAME → the primary record</span></span></label>
            </li>
          </ul>
          <p v-else class="empty-line">No fun names (add them on the Overview tab).</p>
          <div v-if="err('fun_no_dns')" class="err">{{ err('fun_no_dns') }}</div>
          <p class="hint">Fun names switched off stay on the host (prompt, banner, search) but get no DNS record.</p>
        </div>
        <div class="card">
          <div class="row" style="margin-bottom: 10px">
            <h3 style="margin: 0">Extra records</h3><span class="grow"></span>
            <button v-for="[label, preset, hint] in RECORD_PRESETS" :key="label" type="button" class="btn small" :title="hint"
                    @click="addRecord(preset)">+ {{ label }}</button>
          </div>
          <div v-if="form.records.length" class="table-wrap">
            <table class="edit recs">
              <thead><tr><th>Name</th><th>Type</th><th>Value</th><th>Becomes</th><th></th></tr></thead>
              <tbody>
                <tr v-for="(r, n) in form.records" :key="n">
                  <td><input v-model.trim="r.name" class="mono" :placeholder="form.name || 'host'" :class="{ invalid: err(`records[${n}].name`) }" spellcheck="false"></td>
                  <td style="width: 100px">
                    <select v-model="r.type"><option v-for="t in ['CNAME', 'A', 'AAAA', 'TXT', 'SRV', 'MX']" :key="t" :value="t">{{ t }}</option></select>
                  </td>
                  <td><input v-model.trim="r.value" class="mono" :placeholder="recPlaceholder(r)" :class="{ invalid: err(`records[${n}].value`) || err(`records[${n}].type`) }" spellcheck="false"></td>
                  <td class="mono small faint nowrap">{{ recName(r) }}</td>
                  <td><button type="button" class="btn small ghost" aria-label="Remove record" @click="form.records.splice(n, 1)">✕</button></td>
                </tr>
              </tbody>
            </table>
            <template v-for="(r, n) in form.records" :key="`re${n}`">
              <div v-for="f in ['name', 'type', 'value']" :key="f">
                <div v-if="err(`records[${n}].${f}`)" class="err">Row {{ n + 1 }}, {{ f }}: {{ err(`records[${n}].${f}`) }}</div>
              </div>
            </template>
          </div>
          <p v-else class="faint" style="margin: 0">None. Add a name that should point at this host, or a TXT/SRV/MX record.</p>
          <p class="hint">
            Name: relative to <span class="mono">{{ hostZone }}</span> (empty = the host's own name; <code>{host}</code>, <code>{fun}</code> work,
            e.g. <code>_acme-challenge.{host}</code>). Value: <code>{target}</code> = the primary record (what the short name points at),
            <code>{fqdn}</code> = <span class="mono">{{ form.name || 'host' }}.{{ hostZone }}</span>, <code>{ip4}</code>/<code>{ip6}</code> = the primary interface.
            A name with a dot is absolute, a single label is relative to the domain. An empty CNAME value points at the primary record, so it follows when the primary interface changes.
            DNS integrations (Technitium) publish these like every generated record.
          </p>
        </div>
        <div v-if="derived?.dns?.length && !dirty" class="card">
          <h3>All records of this host</h3>
          <div class="table-wrap"><table>
            <thead><tr><th>Name</th><th>Type</th><th>Value</th></tr></thead>
            <tbody><tr v-for="r in derived.dns" :key="r.fqdn + r.type + r.value"><td><Copy :text="r.fqdn" /></td><td>{{ r.type }}</td><td class="mono small">{{ r.value }}</td></tr></tbody>
          </table></div>
        </div>
        <p v-else-if="dirty" class="hint">Save to see the records as they'll be published.</p>
      </fieldset>

      <fieldset v-show="tab === 'services'" class="bare" :disabled="readOnly">
        <section class="tsec">
          <div class="sect-head">
            <h2>Services</h2><span v-if="form.services.length" class="note">{{ form.services.length }}</span><span class="grow"></span>
            <button class="btn small" @click="openSvc(-1)"><Plus :size="14" />Service</button>
          </div>
          <div class="list">
            <div v-for="(sv, n) in form.services" :key="n" class="li" :class="{ bad: errIn(`services[${n}]`) }">
              <AppWindow :size="17" class="li-ic" />
              <div class="li-main">
                <div class="li-title">
                  <span>{{ sv.name || 'unnamed' }}</span>
                  <span v-if="sv.port" class="mono faint">{{ sv.port }}/{{ sv.proto || 'tcp' }}</span>
                  <span v-if="sv.public" class="tag" :title="`${sv.public_name} on WAN port ${sv.public_port || sv.port}`">public</span>
                  <span v-if="sv.publish_srv" class="tag">SRV</span>
                  <span v-if="errIn(`services[${n}]`)" class="small c-red">has errors</span>
                </div>
                <div class="li-sub">
                  <span v-if="sv.description">{{ sv.description }}</span>
                  <span v-if="svcLink(sv)" class="mono">{{ svcLink(sv) }}</span>
                  <span v-if="sv.bind" class="faint">listens on {{ sv.bind === 'primary' ? 'the primary interface' : sv.bind }}</span>
                  <span v-if="(sv.allow_from || []).length">from <span v-for="t in sv.allow_from" :key="t" class="srcchip mono">{{ t }}</span></span>
                </div>
              </div>
              <div class="li-actions">
                <button class="btn icon" :aria-label="`Edit ${sv.name}`" @click="openSvc(n)"><Pencil :size="15" /></button>
                <button class="btn icon" :aria-label="`Remove ${sv.name}`" @click="form.services.splice(n, 1)"><Trash2 :size="15" /></button>
              </div>
            </div>
            <div v-if="!form.services.length" class="list-empty">No services. A service gets a link on the Services page, an optional DNS name and SRV record, and a firewall check of who may reach it.</div>
          </div>
        </section>

        <section class="tsec">
          <div class="sect-head">
            <h2>Firewall rules for the whole host</h2><span class="grow"></span>
            <button class="btn small" @click="openRule(-1, 'in')"><Plus :size="14" />Allow from</button>
            <button class="btn small" @click="openRule(-1, 'out')"><Plus :size="14" />Allow to</button>
          </div>
          <div class="list">
            <div v-for="(r, n) in form.rules" :key="n" class="li" :class="{ bad: errIn(`rules[${n}]`) }">
              <component :is="r.direction === 'out' ? ArrowUpRight : ArrowDownLeft" :size="17" class="li-ic" />
              <div class="li-main">
                <div class="li-title">
                  <span v-if="r.direction === 'out'">To <span class="mono">{{ r.peer || '?' }}</span></span>
                  <span v-else>From <span class="mono">{{ r.peer || '?' }}</span></span>
                  <span class="mono faint">{{ (r.ports || []).join(', ') || 'any' }}</span>
                  <span v-if="errIn(`rules[${n}]`)" class="small c-red">has errors</span>
                </div>
                <div v-if="r.description" class="li-sub">{{ r.description }}</div>
              </div>
              <div class="li-actions">
                <button class="btn icon" aria-label="Edit rule" @click="openRule(n)"><Pencil :size="15" /></button>
                <button class="btn icon" aria-label="Remove rule" @click="form.rules.splice(n, 1)"><Trash2 :size="15" /></button>
              </div>
            </div>
            <div v-if="!form.rules.length" class="list-empty">Access to or from the whole host, beside the services' “allow from”: the backup server may reach every port here, or this host may reach the internet on 443. The OPNsense plugin turns each into pass rules.</div>
          </div>
        </section>
      </fieldset>

      <div v-show="tab === 'ansible'">
          <div class="card">
            <div class="row" style="margin-bottom: 8px">
              <h3 style="margin: 0">Ansible features</h3><span class="grow"></span>
              <RouterLink to="/features" class="small">Feature tree →</RouterLink>
            </div>
            <fieldset :disabled="readOnly" class="bare">
              <div class="row" style="gap: 6px">
                <span v-for="(f, n) in form.features" :key="f" class="chip on mono">{{ f }}
                  <button type="button" class="chipx" aria-label="Remove feature" @click.prevent="form.features.splice(n, 1)">✕</button></span>
                <select v-model="addFeature" class="small auto" aria-label="Add feature"
                        @change="addFeature && !form.features.includes(addFeature) && form.features.push(addFeature); addFeature = ''">
                  <option value="">+ feature…</option>
                  <option v-for="f in featureList.filter((x) => !form.features.includes(x))" :key="f" :value="f">{{ f }}</option>
                </select>
              </div>
              <div v-if="err('features')" class="err">{{ err('features') }}</div>
              <p class="hint">A feature brings its parents: kube.cluster.oak runs kube, kube.cluster and kube.cluster.oak.</p>
              <Field label="Extra vars for this host (YAML)" :error="err('vars')" hint="Win over feature vars. Like host_vars. A secret from the vault: key: {$secret: vault:path#field}.">
                <textarea v-model="form.vars" rows="4" spellcheck="false" placeholder="k3s_role: server&#10;ntp_servers: [10.10.3.1]"></textarea>
              </Field>
            </fieldset>
            <details v-if="derived?.merged_vars && Object.keys(derived.merged_vars).length && !dirty" style="margin-top: 10px">
              <summary class="small muted">Merged variables ({{ derived.features_expanded.join(' → ') || 'host only' }})</summary>
              <pre class="code" style="margin-top: 8px">{{ toYaml(derived.merged_vars) }}</pre>
            </details>
          </div>
      </div>

      <div v-if="!isNew && derived" v-show="tab === 'integrations'">
        <HostModules :host="derived.name" :key="`m${saved}`" />
      </div>

      <div v-if="!isNew && derived && tab === 'secrets'">
        <SecretList :host="derived.name" />
      </div>

      <div v-if="!isNew && derived && tab === 'runs'">
        <RunList :host="derived.name" />
      </div>

      <div v-if="!isNew && derived && tab === 'history'">
        <HistoryList type="host" :name="derived.name" :limit="50" :key="saved" />
      </div>

      <datalist id="port-speeds"><option v-for="x in SPEEDS" :key="x" :value="x" /></datalist>
      <!-- several interfaces from a pattern -->
      <Modal v-if="traceOf" :title="`Trace ${traceOf.port}`" @close="traceOf = null">
        <CableTrace :host="props.name" :port="traceOf.port" />
        <p v-if="dirty" class="small faint" style="margin-bottom: 0">Shows what's saved; save the host to trace your changes.</p>
      </Modal>
      <Modal v-if="bulk" title="Add several interfaces" wide @close="bulk = null">
        <Field label="Names" hint="Ranges in brackets: eth[0-3], 1/0/[1-48], swp[1-4]s[0-3]; a leading zero is kept (Gi1/0/[01-24])">
          <input v-model.trim="bulk.pattern" class="mono" placeholder="eth[0-3]" aria-label="Name pattern"></Field>
        <div class="g3">
          <Field label="Type"><select v-model="bulk.kind"><option v-for="k in IFACE_KINDS" :key="k" :value="k">{{ k }}</option></select></Field>
          <template v-if="isPhysical(bulk)">
            <Field label="Connector"><select :value="bulk.connector" @change="setConnector(bulk, $event.target.value)"><option value="">—</option><option v-for="c in CONNECTORS" :key="c" :value="c">{{ c }}</option></select></Field>
            <Field label="Speed"><input v-model.trim="bulk.speed" list="port-speeds" placeholder="1G"></Field>
          </template>
          <Field v-else label="Sits on"><select v-model="bulk.parent"><option value="">—</option>
            <option v-for="pc in parentChoices({})" :key="pc.value" :value="pc.value">{{ pc.label }}</option></select></Field>
        </div>
        <div v-if="isPhysical(bulk)" class="row panelrow">
          <label class="check"><input v-model="bulk.panel" type="checkbox"> Patch panel: each port gets a rear port it passes through to</label>
          <Field v-if="bulk.panel" label="Rear ports start with" style="width: 170px"><input v-model.trim="bulk.rear" class="mono" placeholder="r"></Field>
        </div>
        <Field label="Network" hint="Optional; addresses are set one by one afterwards">
          <select v-model="bulk.network"><option value="">— none —</option><option v-for="net in netsFor(bulk)" :key="net.name" :value="net.name">{{ net.name }}</option></select></Field>
        <p v-if="bulkNames.length > 256" class="err">At most 256 at once.</p>
        <p v-else-if="bulkNames.length" class="small muted bulkprev">
          {{ bulkNew.length }} new<template v-if="bulk.panel && isPhysical(bulk)"> (plus their rear ports {{ bulk.rear }}{{ bulkNew[0] }} …)</template><template v-if="bulkNames.length > bulkNew.length">, {{ bulkNames.length - bulkNew.length }} there already</template>:
          <span class="mono">{{ bulkNew.slice(0, 12).join(', ') }}{{ bulkNew.length > 12 ? ` … ${bulkNew.at(-1)}` : '' }}</span></p>
        <template #foot>
          <span class="small faint left">Saved with the host</span>
          <button class="btn" @click="bulk = null">Cancel</button>
          <button class="btn primary" :disabled="!bulkNew.length || bulkNames.length > 256" @click="saveBulk">Add {{ bulkNew.length || '' }}</button>
        </template>
      </Modal>

      <!-- one interface -->
      <Modal v-if="ifDlg" :title="ifDlg.idx < 0 ? 'Add interface' : `Edit ${ifDlg.i.label || ifDlg.i.network || 'interface'}`" wide @close="ifDlg = null">
        <div class="g3">
          <Field label="Name" :error="ifErr('label')" hint="eth0, vmbr0, bond0"><input v-model.trim="ifDlg.i.label" class="mono" placeholder="eth0"></Field>
          <Field label="Type" :error="ifErr('kind')">
            <select v-model="ifDlg.i.kind"><option value="">—</option><option v-for="k in IFACE_KINDS" :key="k" :value="k">{{ k }}</option></select></Field>
          <Field label="Sits on" :error="ifErr('parent')" hint="Bond, bridge, NIC or the hypervisor's bridge">
            <select v-model="ifDlg.i.parent">
              <option value="">— (physical)</option>
              <option v-for="pc in parentChoices(ifDlg.i)" :key="pc.value" :value="pc.value">{{ pc.label }}</option>
            </select></Field>
        </div>
        <div v-if="isPhysical(ifDlg.i)" class="g3">
          <Field label="Connector" :error="ifErr('connector')">
            <select :value="ifDlg.i.connector" @change="setConnector(ifDlg.i, $event.target.value)"><option value="">—</option><option v-for="c in CONNECTORS" :key="c" :value="c">{{ c }}</option></select></Field>
          <Field label="Speed" :error="ifErr('speed')" hint="What the port can do; a cable's speed is what it runs at">
            <input v-model.trim="ifDlg.i.speed" list="port-speeds" placeholder="1G"></Field>
          <Field label="Passes through to" :error="ifErr('passthrough')" hint="On a patch panel: the port on the other side">
            <select :value="ifDlg.i.passthrough || ''" @change="setThrough(ifDlg.i, $event.target.value)"><option value="">—</option>
              <option v-for="x in throughChoices(ifDlg.i)" :key="x" :value="x">{{ x }}</option></select></Field>
        </div>
        <div class="g3">
          <Field label="Network" :error="ifErr('network')">
            <select v-model="ifDlg.i.network">
              <option value="">— none —</option>
              <option v-for="net in netsFor(ifDlg.i)" :key="net.name" :value="net.name">{{ net.name }}{{ net.vlan ? ` (VLAN ${net.vlan})` : '' }}{{ net.zone !== form.zone ? ` · ${net.zone}` : '' }}</option>
            </select>
            <label v-if="otherZoneNets" class="check small" style="margin-top: 4px"><input v-model="allNets" type="checkbox"> networks of every zone</label>
          </Field>
          <Field label="MAC" :error="ifErr('mac')"><input v-model.trim="ifDlg.i.mac" class="mono" placeholder="aa:bb:cc:dd:ee:ff" :disabled="isVip"></Field>
          <div class="checks">
            <label class="check"><input type="checkbox" :checked="ifDlg.i.primary" @change="ifDlg.i.primary = $event.target.checked"> Primary (short and fun names point here)</label>
            <label class="check"><input type="checkbox" :checked="ifaceDns(ifDlg.i)" :disabled="!ifDlg.i.network" @change="setIfaceDns(ifDlg.i, $event.target.checked)"> DNS records</label>
          </div>
        </div>
        <!-- addresses per family, like a network's prefixes: the first is the main one (DNS, monitoring), more may follow -->
        <div class="afis">
          <fieldset class="afi">
            <legend>IPv4</legend>
            <Field label="Address" :error="ifErr('ip4')">
              <div class="inline"><input v-model.trim="ifDlg.i.ip4" class="mono" placeholder="10.0.0.10">
                <button type="button" class="btn small" :disabled="!ifDlg.i.network" title="Next free address" @click="nextFree(ifDlg.i)">Next free</button></div>
            </Field>
            <Field label="More IPv4 addresses" :error="extraErr(4)" hint="Secondary: in firewall aliases and IPAM, not in DNS">
              <ChipInput :model-value="extraOf(ifDlg.i, 4)" mono placeholder="10.0.0.11" label="Add an IPv4 address" @update:model-value="(v) => setExtra(ifDlg.i, 4, v)" />
            </Field>
          </fieldset>
          <fieldset class="afi">
            <legend>IPv6</legend>
            <Field label="Interface ID" :error="ifErr('ip6_token')" hint="::53, or eui64 from the MAC; used in every IPv6 prefix of the network">
              <div class="inline"><input v-model.trim="ifDlg.i.ip6_token" class="mono" :placeholder="ifDlg.i.mac ? '::53 or eui64' : '::53'">
                <button type="button" class="btn small" :disabled="!ifDlg.i.network" title="Next free interface ID" @click="nextFree6(ifDlg.i)">Next free</button></div>
            </Field>
            <Field label="Or an address" :error="ifErr('ip6')"><input v-model.trim="ifDlg.i.ip6" class="mono" placeholder="fd00::53"></Field>
            <Field label="More IPv6 addresses" :error="extraErr(6)" hint="Secondary: in firewall aliases and IPAM, not in DNS">
              <ChipInput :model-value="extraOf(ifDlg.i, 6)" mono placeholder="fd00::11" label="Add an IPv6 address" @update:model-value="(v) => setExtra(ifDlg.i, 6, v)" />
            </Field>
            <details v-if="v6Prefixes(ifDlg.i).length" class="v6more">
              <summary class="small">Per prefix</summary>
              <Field v-for="pf in v6Prefixes(ifDlg.i)" :key="pkey(pf)" :label="pf.prefix || `${pf.delegated_from} #${pf.prefix_id}`" :hint="pf.description">
                <input :value="(ifDlg.i.ip6_overrides || {})[pkey(pf)] || ''" class="mono" placeholder="override" @input="setOverride(ifDlg.i, pf, $event.target.value)">
              </Field>
            </details>
          </fieldset>
        </div>
        <div v-for="ip in ifaceIps(ifDlg.i)" :key="'d' + ip" class="small muted">
          <template v-if="derived?.seen?.[ip]">
            Discovery saw <span class="mono">{{ ip }}</span>{{ derived.seen[ip].hostname ? ` as ${derived.seen[ip].hostname}` : '' }}
            <template v-if="derived.seen[ip].vendor">, {{ derived.seen[ip].vendor }} <a v-if="!form.vendor" href="#" @click.prevent="form.vendor = derived.seen[ip].vendor">use as vendor</a></template>
            <template v-if="derived.seen[ip].mac">, <span class="mono">{{ derived.seen[ip].mac }}</span> <a v-if="!ifDlg.i.mac" href="#" @click.prevent="ifDlg.i.mac = derived.seen[ip].mac">use</a></template>
            ({{ derived.seen[ip].source }}, {{ ago(derived.seen[ip].last_seen) }})
          </template>
        </div>
        <template #foot>
          <span class="small faint left">Saved with the host</span>
          <button class="btn" @click="ifDlg = null">Cancel</button>
          <button class="btn primary" @click="saveIf">{{ ifDlg.idx < 0 ? 'Add interface' : 'Done' }}</button>
        </template>
      </Modal>

      <Modal v-if="cabDlg" :title="cabDlg.idx < 0 ? (isInternal(cabDlg.c) ? 'Add internal link' : 'Add cable') : (isInternal(cabDlg.c) ? 'Edit internal link' : 'Edit cable')" @close="cabDlg = null">
        <div class="seg" role="radiogroup" aria-label="Kind of link">
          <button type="button" role="radio" :aria-checked="!isInternal(cabDlg.c)" :class="{ on: !isInternal(cabDlg.c) }" @click="setCableType(false)">Cable</button>
          <button type="button" role="radio" :aria-checked="isInternal(cabDlg.c)" :class="{ on: isInternal(cabDlg.c) }" :disabled="!internalPeers.length"
                  :title="internalPeers.length ? '' : 'Only between a guest and the host it runs on (set “Runs on” first)'" @click="setCableType(true)">Internal</button>
        </div>
        <p v-if="isInternal(cabDlg.c)" class="small muted" style="margin: 0">A link inside one machine: this guest's interface on the hypervisor's bridge
          (or two guests of one host). The topology draws it inside the hypervisor's box.</p>
        <div class="g2">
          <Field :label="isInternal(cabDlg.c) ? 'Interface here' : 'Port here'" :error="err(`cables[${cabDlg.idx}].port`)"><input v-model.trim="cabDlg.c.port" class="mono" list="own-ports" placeholder="eth0"></Field>
          <Field v-if="!isInternal(cabDlg.c)" label="Medium"><select v-model="cabDlg.c.medium"><option value="">—</option><option v-for="m in MEDIA" :key="m" :value="m">{{ m }}</option></select></Field>
          <Field v-if="isInternal(cabDlg.c)" label="Other host" :error="err(`cables[${cabDlg.idx}].peer`)"><select v-model="cabDlg.c.peer"><option value="" disabled>pick a host</option>
            <option v-for="h in internalPeers" :key="h.name" :value="h.name">{{ h.name }} ({{ h.why }})</option></select></Field>
          <Field v-else label="Other host" :error="err(`cables[${cabDlg.idx}].peer`)"><select v-model="cabDlg.c.peer"><option value="" disabled>pick a host</option>
            <option v-if="form.name" :value="form.name">{{ form.name }} (this host: port to port)</option>
            <option v-for="h in hostNames.filter((n) => n !== form.name)" :key="h" :value="h">{{ h }}</option></select></Field>
          <Field :label="isInternal(cabDlg.c) ? 'Its interface (bridge)' : 'Its port'" :error="err(`cables[${cabDlg.idx}].peer_port`)">
            <input v-model.trim="cabDlg.c.peer_port" class="mono" list="peer-ports" :placeholder="isInternal(cabDlg.c) ? 'vmbr0' : 'ge-0/0/12'"></Field>
          <Field v-if="!isInternal(cabDlg.c)" label="Speed"><input v-model.trim="cabDlg.c.speed" list="cable-speeds" placeholder="1G"></Field>
          <Field label="Notes"><input v-model="cabDlg.c.notes" :placeholder="isInternal(cabDlg.c) ? 'VLAN-aware, tag 100' : 'patch panel 12'"></Field>
        </div>
        <CableExtras v-if="!isInternal(cabDlg.c)" v-model:cable="cabDlg.c" :errors="{ label: err(`cables[${cabDlg.idx}].label`), length: err(`cables[${cabDlg.idx}].length`) }" />
        <datalist id="cable-speeds"><option v-for="x in SPEEDS" :key="x" :value="x" /></datalist>
        <datalist id="own-ports"><option v-for="x in form.interfaces.filter((y) => y.label)" :key="x.label" :value="x.label" /></datalist>
        <datalist id="peer-ports"><option v-for="x in peerPorts" :key="x.label" :value="x.label">{{ x.kind }}</option></datalist>
        <template #foot>
          <span class="small faint left">Saved with the host</span>
          <button class="btn" @click="cabDlg = null">Cancel</button>
          <button class="btn primary" :disabled="!cabDlg.c.port || !cabDlg.c.peer || !cabDlg.c.peer_port" @click="saveCable">{{ cabDlg.idx < 0 ? 'Add cable' : 'Done' }}</button>
        </template>
      </Modal>

      <Modal v-if="svcDlg" :title="svcDlg.idx < 0 ? 'Add service' : `Edit ${svcDlg.s.name || 'service'}`" wide @close="svcDlg = null">
        <ServiceForm v-model:s="svcDlg.s" :prefix="`services[${svcDlg.idx}]`" :errors="errors" :host-zone="hostZone"
                     :interfaces="form.interfaces.map((i, n) => ({ ...i, ip6_all: (preview?.ip6?.[n] || []).map((x) => x.address) }))" />
        <template #foot>
          <span class="small faint left">Saved with the host</span>
          <button class="btn" @click="svcDlg = null">Cancel</button>
          <button class="btn primary" :disabled="!svcDlg.s.name" @click="saveSvc">{{ svcDlg.idx < 0 ? 'Add service' : 'Done' }}</button>
        </template>
      </Modal>

      <Modal v-if="ruleDlg" :title="ruleDlg.r.direction === 'out' ? 'Allow this host to reach' : 'Allow to reach this host'" @close="ruleDlg = null">
        <Field label="Direction"><select v-model="ruleDlg.r.direction"><option value="in">from somewhere to this host</option><option value="out">from this host to somewhere</option></select></Field>
        <Field :label="ruleDlg.r.direction === 'out' ? 'To' : 'From'" :error="err(`rules[${ruleDlg.idx}].peer`)" hint="A zone, area, network, alias, address, CIDR or any">
          <input v-model.trim="ruleDlg.r.peer" class="mono" list="rule-peers" placeholder="CA_ST"></Field>
        <datalist id="rule-peers"><option v-for="pe in peers" :key="pe" :value="pe" /></datalist>
        <Field label="Ports" :error="err(`rules[${ruleDlg.idx}].ports`)" hint="Empty = any; port aliases or tcp/443, udp/53, tcp/8000-8100">
          <ChipInput v-model="ruleDlg.r.ports" mono :suggestions="['any', 'tcp/22', 'tcp/443', 'udp/53', ...(srcQ.data.value?.aliases || [])]" placeholder="any" label="Add a port" :normalize="(v) => v.trim()" />
        </Field>
        <Field label="Why"><input v-model="ruleDlg.r.description" placeholder="backups pull from here"></Field>
        <template #foot>
          <span class="small faint left">Saved with the host</span>
          <button class="btn" @click="ruleDlg = null">Cancel</button>
          <button class="btn primary" :disabled="!ruleDlg.r.peer" @click="saveRule">{{ ruleDlg.idx < 0 ? 'Add rule' : 'Done' }}</button>
        </template>
      </Modal>
    </template>
  </div>
</template>

<style scoped>
.ifgroup { margin: 12px 0 6px; font-weight: 600; }
.ifgroup:first-of-type { margin-top: 0; }
.bulkprev { margin: 0; overflow-wrap: anywhere; }
.tsec + .tsec { margin-top: 28px; }
.rackrow { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 6px; }
.rackrow > select:first-child { grid-column: 1 / -1; } /* the rack on its own line, unit and side below */
.afis { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 4px; }
.afi { border: 1px solid var(--line); border-radius: var(--r-box); padding: 4px 14px 12px; margin: 0; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
.afi legend { font-weight: 600; font-size: 13px; padding: 0 6px; }
@media (max-width: 700px) { .afis { grid-template-columns: minmax(0, 1fr); } }
.spd { display: inline-block; width: 14px; height: 4px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
.tsec .sect-head h2 { font-size: 15px; }
.li-ic { color: var(--faint); flex: none; margin-top: 2px; }
.li.bad { box-shadow: inset 3px 0 0 var(--err); }
.li .cab { display: inline-flex; align-items: center; gap: 4px; }
.li .seen { display: inline-flex; align-items: center; color: var(--faint); }
.li .seen .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--faint); margin-right: 5px; }
.li .seen .dot.up { background: var(--ok); }
.srcchip { display: inline-block; padding: 0 6px; margin-left: 4px; border-radius: 4px; background: var(--raise); color: var(--muted); font-size: 12px; }
.inline { display: flex; gap: 6px; }
.checks { display: flex; flex-direction: column; gap: 6px; padding-top: 24px; }
.v6more summary { cursor: pointer; color: var(--muted); }
.v6more > * + * { margin-top: 10px; }
.stack-tree .sp { margin-left: 6px; }
.iconpick { display: flex; flex-wrap: wrap; gap: 4px; }
.ip { width: 32px; height: 32px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 7px; background: none; color: var(--muted); cursor: pointer; }
.ip.on { border-color: var(--accent); color: var(--ink); }
.lower1 > .card { margin-top: 16px; }
.hstate { font-size: 11.5px; font-weight: 600; padding: 1px 8px; border-radius: 6px; background: rgba(255, 255, 255, .05); color: var(--faint); }
.hstate.up { color: var(--ok); background: rgba(74, 222, 128, .1); }
.hstate.down { color: var(--err); background: rgba(248, 113, 113, .12); }
.span2 { grid-column: 1 / -1; }
.flabel { display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 4px; }
.builder { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.builder { flex-wrap: nowrap; }
.builder select, .builder .role-in { flex: 1; min-width: 0; font-family: var(--mono); }
/* a phone: category and type on one line, role and number on the next, no dashes between */
@media (max-width: 640px) { .builder { flex-wrap: wrap; gap: 6px; } .builder .dash { display: none; } .builder select, .builder .role-in { flex: 1 1 calc(50% - 6px); } }
.tcount { margin-left: 6px; font-size: 11px; color: var(--faint); font-weight: 600; }
.builder .nn { width: 60px; }
.dash { color: var(--faint); font-family: var(--mono); }
.v6row { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; margin-top: 6px; }
.v6addr { padding: 1px 6px; border: 1px solid var(--line); border-radius: 4px; }
.v6more { flex-basis: 100%; display: flex; flex-wrap: wrap; gap: 6px 14px; padding: 6px 0 2px; }
.v6more label { display: flex; gap: 6px; align-items: center; color: var(--faint); }
.v6more input { width: 220px !important; padding: 3px 6px !important; font-size: 12.5px !important; }
.sugg { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 8px; padding: 8px; border: 1px dashed var(--line); border-radius: 8px; }
.fundns { display: flex; flex-wrap: wrap; column-gap: 8px; min-width: 0; overflow-wrap: anywhere; }
.zsel { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; margin-top: 6px; max-width: 460px; }
</style>
