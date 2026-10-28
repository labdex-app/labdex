<script setup>
// The lab as a picture, two ways:
//   Physical: hypervisors are boxes with their guests and their own interfaces inside; cables
//             join ports (coloured by speed); internal links join a guest to its bridge.
//   L3/VLANs: networks as nodes, every host joined to the networks it has an address in.
// Drag things where you like them: each view's arrangement is shared, everyone sees the same map
// (people who edit hosts move it; for others a drag only lasts until the page reloads).
// People who edit hosts draw cables here too: Cable, then click both ends.
import { Cable, Download, LocateFixed, MoreHorizontal, RefreshCw, Route, Save, Server, Trash2, X } from 'lucide-vue-next'
import CableTrace from '../components/CableTrace.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CableExtras from '../components/CableExtras.vue'
import Field from '../components/Field.vue'
import FilterSelect from '../components/FilterSelect.vue'
import Modal from '../components/Modal.vue'
import MenuButton from '../components/MenuButton.vue'
import ZoneChip from '../components/ZoneChip.vue'
import { useQuery } from '../query'
import { api } from '../api'
import { confirm } from '../confirm'
import { iconFor, iconPicture } from '../icons'
import { MEDIA, SPEEDS, SPEED_STEPS, cableHex, speedMbps, speedStep } from '../links'
import { state, toast, zoneHex } from '../store'
import { shown as shownTheme } from '../theme'

const route = useRoute()
const router = useRouter()
const zone = ref(route.query.zone || null)
const view = ref(route.query.view === 'l3' ? 'l3' : 'physical')
const lonely = ref(false)
// per browser: the grid (and snapping to it) and the networks hidden from the L3 view
const pref = (k, d) => { try { const v = localStorage.getItem(`labdex-topo-${k}`); return v == null ? d : JSON.parse(v) } catch { return d } }
const grid = ref(pref('grid', false))
const lines = ref(pref('lines', 'cable')) // what colours a cable: 'cable' (its own colour, if it has one) or 'speed'
watch(lines, (v) => { try { localStorage.setItem('labdex-topo-lines', JSON.stringify(v)) } catch { /* */ } recolour() })
const hiddenNets = ref(pref('hidden-nets', []))
watch(grid, (v) => { try { localStorage.setItem('labdex-topo-grid', JSON.stringify(v)) } catch { /* */ } })
watch(hiddenNets, (v) => { try { localStorage.setItem('labdex-topo-hidden-nets', JSON.stringify(v)) } catch { /* */ } }, { deep: true })
const GRID = 24 // model units between grid lines
const tracing = ref(null) // {host, port}: the trace dialog
const hover = ref(null) // {n, x, y}: the card next to a host under the pointer

// --------------------------------------------------------------- saved views
// a view: picture, zone, hidden networks, options and the camera; shared, opened by link (?v=<uid>)
const viewsQ = useQuery('/api/topology/views')
const views = computed(() => viewsQ.data.value?.views || [])
const current = ref(null) // the saved view in use
let camera = null // to apply once the picture is drawn
function applyView(v) {
  const c = v.config || {}
  current.value = v
  camera = c.camera || null
  hiddenNets.value = c.hidden_nets || []
  lonely.value = !!c.lonely
  grid.value = !!c.grid
  if (c.lines) lines.value = c.lines
  zone.value = c.zone || null
  if (view.value !== c.view) view.value = c.view || 'physical'
  else nextTick(draw)
}
function pickView(uid) {
  if (!uid) { current.value = null; router.replace({ query: { ...route.query, v: undefined } }); return }
  const v = views.value.find((x) => x.uid === uid)
  if (v) applyView(v)
}
watch(views, (list) => { // opened by link
  const uid = route.query.v
  if (uid && !current.value) { const v = list.find((x) => x.uid === uid); if (v) applyView(v) }
}, { immediate: true })
const viewDlg = ref(null)
function configNow() {
  const c = cy.value
  return { view: view.value, zone: zone.value || '', hidden_nets: hiddenNets.value, lonely: lonely.value, grid: grid.value, lines: lines.value,
    camera: c ? { zoom: c.zoom(), x: c.pan().x, y: c.pan().y } : undefined }
}
async function saveView(asNew) {
  const d = viewDlg.value
  try {
    const body = { name: d.name, config: configNow() }
    const v = !asNew && current.value ? await api.put(`/api/topology/views/${current.value.uid}`, body) : await api.post('/api/topology/views', body)
    current.value = v
    viewDlg.value = null
    toast(`Saved the view ${v.name}`)
    viewsQ.refresh()
  } catch (x) { d.error = x.fields?.name || x.message }
}
async function deleteView() {
  const v = current.value
  if (!(await confirm({ title: `Delete the view ${v.name}?`, body: 'The map itself stays as it is.', action: 'Delete', danger: true }))) return
  await api.del(`/api/topology/views/${v.uid}`)
  current.value = null
  viewsQ.refresh()
}

// ------------------------------------------------------------------- export
// canvas2svg (inside cytoscape-svg) loses a node's border: the border is stroked after the icon is
// drawn, when its current element is no longer the shape's path. Then the shape is drawn once more.
function patchC2S() {
  const C2S = window.C2S
  if (!C2S || C2S.prototype.__labdexPatched) return
  const apply = C2S.prototype.__applyCurrentDefaultPath
  C2S.prototype.__applyCurrentDefaultPath = function () {
    if (this.__currentElement.nodeName !== 'path' && this.__currentDefaultPath) {
      const p = this.__createElement('path', {}, true)
      this.__closestGroupOrSvg().appendChild(p)
      this.__currentElement = p
    }
    return apply.call(this)
  }
  C2S.prototype.__labdexPatched = true
}
async function exportAs(kind) {
  const c = cy.value
  if (!c) return
  const name = `labdex-${view.value}${zone.value ? `-${zone.value}` : ''}${current.value ? `-${current.value.name.replace(/[^\w-]+/g, '_')}` : ''}`
  let blob
  if (kind === 'png') blob = c.png({ output: 'blob', full: true, scale: 2, bg: css('--bg') })
  else {
    const [{ default: cytoscape }, { default: svg }] = await Promise.all([import('cytoscape'), import('cytoscape-svg')])
    try { cytoscape.use(svg) } catch { /* already registered */ }
    patchC2S()
    blob = new Blob([c.svg({ full: true, scale: 1, bg: css('--bg') })], { type: 'image/svg+xml' })
  }
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${name}.${kind}`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
const q = ref('')
const query = useQuery(() => `/api/topology?view=${view.value}${zone.value ? `&zone=${encodeURIComponent(zone.value)}` : ''}`,
  { onError: (e) => toast(e.message, 'error') })
const data = computed(() => query.data.value)
const box = ref(null)
const cy = shallowRef(null)
const sel = ref(null)
const local = { physical: {}, l3: {} } // drags that aren't saved (you can't arrange the shared map), and the last saved spots
let fresh = false // Arrange again: ignore the saved layout once
let saveTimer = null

const zones = computed(() => (state.meta?.zones || []).map((z) => z.code))
const stats = computed(() => {
  const d = data.value
  if (!d) return null
  if (d.view === 'l3') {
    return { hosts: d.nodes.filter((n) => n.type === 'host').length, networks: d.nodes.filter((n) => n.type === 'network').length,
             routers: d.nodes.filter((n) => n.router).length }
  }
  return { boxes: d.nodes.filter((n) => n.type === 'box').length, hosts: d.nodes.filter((n) => n.type === 'host').length,
           cables: d.edges.filter((e) => e.kind === 'cable').length, internal: d.edges.filter((e) => e.kind === 'internal').length,
           lonely: d.nodes.filter((n) => n.lonely).length }
})
const speedsUsed = computed(() => {
  const cables = (data.value?.edges || []).filter((e) => e.kind === 'cable')
  const used = new Set(cables.map((e) => speedStep(e.speed)?.label || ''))
  const cols = new Set(cables.map((e) => cableHex(e.cable_colour) ? (e.cable_colour || '').toLowerCase() : ''))
  return { steps: SPEED_STEPS.filter((s) => used.has(s.label)), unknown: used.has(''), any: cables.length > 0,
    colours: [...cols].filter(Boolean).map((c) => ({ label: c, colour: cableHex(c) })), uncoloured: cols.has('') }
})

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888' }
function saved() { return fresh ? {} : { ...(data.value?.layout || {}), ...local[view.value] } }
const canArrange = computed(() => !!data.value?.can_arrange)

const allNets = computed(() => (data.value?.view === 'l3' ? data.value.nodes.filter((n) => n.type === 'network').map((n) => n.name) : []))
function toggleNet(name) { hiddenNets.value = hiddenNets.value.includes(name) ? hiddenNets.value.filter((x) => x !== name) : [...hiddenNets.value, name] }
function elements(d) {
  let nodes = d.nodes.filter((n) => d.view === 'l3' || lonely.value || !n.lonely)
  let edges = d.edges
  if (d.view === 'l3' && hiddenNets.value.length) { // a hidden network takes its lines along, and hosts left with none
    const gone = new Set(hiddenNets.value.map((x) => `n:${x}`))
    edges = edges.filter((e) => !gone.has(e.target))
    const linked = new Set(edges.flatMap((e) => [e.source, e.target]))
    nodes = nodes.filter((n) => !gone.has(n.id) && (n.type !== 'host' || linked.has(n.id)))
  }
  const ids = new Set(nodes.map((n) => n.id))
  const ink = css('--ink')
  const net = (n) => [n.label, n.vlan != null ? `VLAN ${n.vlan}` : '', n.prefix].filter(Boolean).join('  ·  ')
  return [
    ...nodes.map((n) => ({ group: 'nodes', data: { ...n, parent: n.parent || undefined, colour: n.colour || (n.zone ? zoneHex(n.zone) : css('--line-strong')),
      picture: n.type === 'host' ? iconPicture(iconFor({ name: n.name, icon: n.icon, kind: n.kind }), ink) : undefined,
      // a host: its hostname below it (its fun name is a node of its own, see below); a hypervisor box: both on one line
      text: n.type === 'box' ? `${n.label}${n.label !== n.name ? `  ${n.name}` : ''}` : n.type === 'network' ? net(n)
        : n.type === 'host' && n.fun ? n.name : n.label } })),
    // a host's fun name: its own little node, above the host by default; drag it anywhere around the host and it follows the host
    // (not inside the host's box: the box's size and the layout go by what's really in it)
    ...nodes.filter((n) => n.type === 'host' && n.fun).map((n) => ({ group: 'nodes', data: { id: `t:${n.id}`, type: 'name', of: n.id, text: n.fun } })),
    ...edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ group: 'edges', data: { ...e,
      // port names at the ends that are hosts (a port node inside a hypervisor names itself)
      ...(e.source === e.target ? { loop: `${e.a_port} – ${e.b_port}` } // a cable inside one host: one label at the loop
        : { src: e.source.startsWith('h:') && e.kind !== 'address' ? e.a_port || '' : '', dst: e.target.startsWith('h:') && e.kind !== 'address' ? e.b_port || '' : '' }),
      ...lineColour(e),
      weight: e.kind === 'cable' ? Math.min(1.6 + Math.log10(Math.max(speedMbps(e.speed), 100) / 100) * 1.1, 5) : undefined } })),
  ]
}

// what a cable is drawn in: its own colour (white and black get a thin edge so they show on both themes), or its speed
function lineColour(e) {
  if (e.kind !== 'cable') return { colour: undefined, outline: undefined }
  if (lines.value === 'speed') return { colour: speedStep(e.speed)?.colour, outline: undefined }
  const hex = cableHex(e.cable_colour)
  const pale = ['white', 'black', '#f5f5f5', '#262626', '#ffffff', '#000000'].includes(String(e.cable_colour || '').toLowerCase())
  return { colour: hex || undefined, outline: pale ? css('--muted') : undefined }
}
function recolour() {
  cy.value?.edges('[kind = "cable"]').forEach((ed) => {
    for (const [k, v] of Object.entries(lineColour(ed.data()))) { if (v) ed.data(k, v); else ed.removeData(k) }
  })
}

// where a host's fun name sits, relative to the host: {host node id: {x, y}}; saved with the layout as "t:<host node id>"
let offsets = {}
const defaultOffset = (host) => ({ x: 0, y: -(host.outerHeight() / 2 + 12) })
function placeName(host) {
  const t = cy.value?.getElementById(`t:${host.id()}`)
  if (!t || t.empty() || t.grabbed()) return
  const o = offsets[host.id()] || defaultOffset(host), p = host.position()
  t.position({ x: p.x + o.x, y: p.y + o.y })
}

async function draw() {
  const d = data.value
  if (!d || !box.value) return
  const [{ default: cytoscape }, { default: fcose }] = await Promise.all([import('cytoscape'), import('cytoscape-fcose')])
  try { cytoscape.use(fcose) } catch { /* already registered */ }
  cy.value?.destroy()
  const ink = css('--ink'), muted = css('--muted'), line = css('--line-strong'), accent = css('--accent'), panel = css('--panel'), card = css('--card'), bg = css('--bg')
  const c = cytoscape({
    container: box.value,
    elements: elements(d),
    wheelSensitivity: 0.25,
    minZoom: 0.15,
    maxZoom: 3,
    style: [
      { selector: 'node', style: { 'font-family': 'IBM Plex Sans, system-ui, sans-serif', 'font-size': 11, color: ink, label: 'data(text)' } },
      { selector: 'node[type = "host"]', style: { shape: 'round-rectangle', width: 34, height: 34, 'background-color': card, 'border-width': 2,
        'border-color': 'data(colour)', 'background-image': 'data(picture)', 'background-width': '62%', 'background-height': '62%',
        'text-valign': 'bottom', 'text-margin-y': 5, 'text-wrap': 'wrap', 'text-max-width': 150, 'line-height': 1.25, 'font-size': 10,
        'text-background-color': bg, 'text-background-opacity': 0.85, 'text-background-padding': 1.5, 'text-background-shape': 'roundrectangle' } },
      // with a fun name above it, the hostname below is the technical one: mono and quieter
      { selector: 'node[type = "host"][?fun]', style: { 'font-family': 'IBM Plex Mono, monospace', 'font-size': 9, color: muted } },
      { selector: 'node[type = "name"]', style: { shape: 'round-rectangle', width: 'label', height: 'label', padding: 3, 'background-color': bg,
        'background-opacity': 0.85, 'border-width': 0, 'font-size': 11.5, 'font-weight': 600, color: ink, 'text-valign': 'center', 'text-halign': 'center' } },
      { selector: 'node[type = "host"][kind = "vip"]', style: { shape: 'ellipse', 'border-style': 'dashed' } },
      { selector: 'node[type = "host"][?router]', style: { 'border-width': 3, shape: 'round-diamond', width: 42, height: 42 } },
      { selector: 'node[type = "host"][status = "offline"], node[type = "host"][status = "planned"]', style: { 'border-style': 'dashed', opacity: 0.75 } },
      { selector: 'node[type = "box"]', style: { shape: 'round-rectangle', 'background-color': panel, 'background-opacity': 0.9, 'border-width': 1.5,
        'border-color': 'data(colour)', padding: 28, 'text-valign': 'top', 'text-halign': 'center', 'text-margin-y': -6, 'font-weight': 600,
        'font-size': 12, 'corner-radius': 10 } },
      { selector: 'node[type = "port"]', style: { shape: 'round-rectangle', width: 'label', height: 16, padding: 4, 'background-color': card,
        'border-width': 1, 'border-color': line, 'font-family': 'IBM Plex Mono, monospace', 'font-size': 9.5, 'text-valign': 'center', color: muted } },
      { selector: 'node[type = "port"][kind = "bridge"]', style: { 'border-color': accent, color: ink } },
      { selector: 'node[type = "network"]', style: { shape: 'round-rectangle', width: 'label', height: 26, padding: 10, 'background-color': 'data(colour)',
        'background-opacity': 0.22, 'border-width': 1.5, 'border-color': 'data(colour)', 'text-valign': 'center', 'font-weight': 600,
        'font-family': 'IBM Plex Mono, monospace', 'font-size': 10.5 } },
      { selector: 'edge', style: { width: 1.6, 'line-color': line, 'curve-style': 'bezier', 'font-size': 9, color: muted, 'text-background-color': card,
        'text-background-opacity': 1, 'text-background-padding': 2, 'font-family': 'IBM Plex Mono, monospace' } },
      { selector: 'edge[kind = "cable"][colour]', style: { 'line-color': 'data(colour)' } },
      { selector: 'edge[kind = "cable"][outline]', style: { 'line-outline-width': 0.75, 'line-outline-color': 'data(outline)' } },
      { selector: 'edge[kind = "cable"][weight]', style: { width: 'data(weight)' } },
      { selector: 'edge[kind = "cable"][medium = "wifi"]', style: { 'line-style': 'dashed', 'line-dash-pattern': [2, 4] } },
      { selector: 'edge[kind = "internal"]', style: { 'line-style': 'dashed', 'line-color': accent, width: 1.4 } },
      { selector: 'edge[?auto]', style: { opacity: 0.5, 'line-dash-pattern': [3, 5] } },
      { selector: 'edge[kind = "vip"]', style: { 'line-style': 'dotted', 'line-color': accent, width: 1.6 } },
      // port names: small tags out past the host and its hostname, so they don't sit on either
      { selector: 'edge[src], edge[dst]', style: { 'source-label': 'data(src)', 'target-label': 'data(dst)', 'source-text-offset': 48, 'target-text-offset': 48,
        'min-zoomed-font-size': 7, 'font-size': 8.5, 'text-background-shape': 'roundrectangle', 'text-background-padding': 2.5, 'text-border-width': 1,
        'text-border-color': line, 'text-border-opacity': 1, color: ink } },
      { selector: 'edge[loop]', style: { 'loop-direction': '0deg', 'loop-sweep': '-50deg', 'control-point-step-size': 46, label: 'data(loop)',
        'min-zoomed-font-size': 8, 'font-size': 8.5 } },
      { selector: 'edge[kind = "stack"]', style: { 'line-style': 'dotted', width: 1.2 } },
      { selector: 'edge[kind = "address"]', style: { width: 1.3, 'line-color': line } },
      { selector: 'edge.show, edge:selected', style: { label: 'data(label)', color: ink } },
      { selector: 'edge:selected', style: { 'underlay-color': accent, 'underlay-opacity': 0.35, 'underlay-padding': 3 } },
      { selector: 'node:selected', style: { 'border-color': accent, 'border-width': 3 } },
      { selector: 'node[type = "name"]:selected, node[type = "name"].hit', style: { 'border-width': 0 } },
      { selector: '.dim', style: { opacity: 0.18 } },
      { selector: '.hit, .from', style: { 'border-color': accent, 'border-width': 3 } },
    ],
  })
  cy.value = c
  const pos = saved()
  offsets = Object.fromEntries(Object.entries(pos).filter(([k]) => k.startsWith('t:')).map(([k, v]) => [k.slice(2), v]))
  const names = c.nodes('[type = "name"]')
  const placed = c.elements().not(names) // the names are placed next to their hosts afterwards
  const known = placed.nodes().filter((n) => !n.isParent() && pos[n.id()])
  if (known.length && known.length === placed.nodes().filter((n) => !n.isParent()).length) {
    placed.layout({ name: 'preset', positions: (n) => pos[n.id()], fit: false }).run()
    c.nodes('[type = "host"]').forEach(placeName)
    c.fit(undefined, 44)
  } else {
    placed.layout({ name: 'fcose', quality: 'proof', animate: false, nodeDimensionsIncludeLabels: true, packComponents: true,
      idealEdgeLength: (e) => (e.data('kind') === 'stack' ? 30 : e.data('kind') === 'internal' ? 60 : e.data('kind') === 'address' ? 90 : 120),
      nodeRepulsion: () => 9000, nestingFactor: 0.6, tilingPaddingVertical: 20, tilingPaddingHorizontal: 20, padding: 30,
      fixedNodeConstraint: known.length ? known.map((n) => ({ nodeId: n.id(), position: pos[n.id()] })) : undefined }).run()
    c.nodes('[type = "host"]').forEach(placeName)
    c.fit(undefined, 44)
    const startOver = fresh
    fresh = false
    if (canArrange.value) remember(startOver ? 'replace' : 'all') // new nodes found their place: save it for everyone
  }
  c.on('position', 'node[type = "host"]', (e) => placeName(e.target))
  c.on('dragfree', 'node', (e) => {
    const n = e.target
    if (n.data('type') === 'name') { // a fun name moved: remember where it sits relative to its host
      const h = c.getElementById(n.data('of')), p = n.position(), hp = h.position()
      offsets[h.id()] = { x: p.x - hp.x, y: p.y - hp.y }
      remember('name', n)
      return
    }
    if (grid.value) snap(n)
    remember('moved', n)
  })
  const hostOf = (node) => (node.data('type') === 'name' ? node.cy().getElementById(node.data('of')) : node)
  c.on('tap', 'node', (e) => tapNode(hostOf(e.target)))
  c.on('tap', 'edge', (e) => { if (!wiring.value) sel.value = { ...e.target.data(), type: 'edge' } })
  c.on('viewport', paintGrid)
  c.on('mouseover', 'node[type = "host"], node[type = "box"]', (e) => {
    if (wiring.value) return
    const p = e.target.renderedPosition(), half = e.target.renderedOuterHeight() / 2
    const up = p.y + half + 130 > c.height() // near the bottom: the card goes above
    hover.value = { n: e.target.data(), x: Math.min(Math.max(p.x, 150), c.width() - 150), y: up ? p.y - half - 8 : p.y + half + 8, up }
  })
  c.on('mouseout', 'node', () => { hover.value = null })
  c.on('drag pan zoom', () => { hover.value = null })
  paintGrid()
  c.on('tap', (e) => { if (e.target === c) { sel.value = null; if (wiring.value) stopWiring() } })
  c.on('dbltap', 'node', (e) => {
    const n = hostOf(e.target).data()
    if (wiring.value) return
    if (n.type === 'network') router.push(`/networks/${n.uid || n.name}`)
    else if (n.type !== 'port') router.push(`/hosts/${n.uid || n.name || n.host}`)
  })
  c.on('mouseover', 'edge', (e) => e.target.addClass('show'))
  c.on('mouseout', 'edge', (e) => e.target.removeClass('show'))
  if (camera) { c.viewport({ zoom: camera.zoom, pan: { x: camera.x, y: camera.y } }); camera = null }
  highlight()
}

const pending = {}
const savedNote = ref('')
// what = 'moved' (one node and, for a box, what's inside it), 'all' (every node), 'replace' (start over with these)
function remember(what, node) {
  const c = cy.value
  if (!c) return
  const v = view.value
  const real = (n) => !n.isParent() && n.data('type') !== 'name'
  if (what === 'name') {
    const k = `t:${node.data('of')}`, o = offsets[node.data('of')]
    pending[k] = { ...o }
    local[v][k] = { ...o }
  } else {
    const nodes = what === 'moved' ? node.union(node.descendants()).filter(real) : c.nodes().filter(real)
    nodes.forEach((n) => { pending[n.id()] = { ...n.position() }; local[v][n.id()] = { ...n.position() } })
    if (what !== 'moved') for (const [k, o] of Object.entries(offsets)) { pending[`t:${k}`] = { ...o }; local[v][`t:${k}`] = { ...o } }
  }
  if (!canArrange.value) { savedNote.value = 'Only people who edit hosts arrange the shared map; your drag lasts until you reload.'; return }
  const replace = what === 'replace'
  clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    const positions = { ...pending }
    for (const k of Object.keys(pending)) delete pending[k]
    try {
      await api.put(`/api/topology/layout?view=${v}`, { positions, replace })
      if (data.value?.view === v) data.value.layout = { ...(replace ? {} : data.value.layout), ...positions } // no refetch, no redraw
      savedNote.value = 'Saved for everyone'
      setTimeout(() => { savedNote.value = '' }, 1500)
    } catch (e) { toast(e.message, 'error') }
  }, 400)
}
async function arrange() {
  if (canArrange.value && !(await confirm({ title: 'Arrange the map again?', body: 'Everyone’s shared arrangement of this view is replaced by a fresh layout.', action: 'Arrange again' }))) return
  local[view.value] = {}
  offsets = {}
  fresh = true
  draw()
}
function highlight() {
  const c = cy.value
  if (!c) return
  const n = q.value.trim().toLowerCase()
  c.elements().removeClass('dim hit')
  if (!n) return
  const hits = c.nodes('[type != "name"]').filter((x) => `${x.data('name') || ''} ${x.data('fun') || ''} ${x.data('label') || ''} ${x.data('address') || ''} ${x.data('prefix') || ''}`.toLowerCase().includes(n))
  if (!hits.length) return
  c.elements().addClass('dim')
  const near = hits.union(hits.ancestors()).union(hits.connectedEdges()).union(hits.neighborhood())
  near.union(c.nodes('[type = "name"]').filter((t) => near.has(c.getElementById(t.data('of'))))).removeClass('dim')
  hits.addClass('hit')
  c.animate({ fit: { eles: hits, padding: 120 }, duration: 250 })
}
// the grid moves and scales with the picture; dropping a node puts it on the nearest crossing
function paintGrid() {
  const el = box.value?.parentElement, c = cy.value
  if (!el || !c) return
  const z = c.zoom(), p = c.pan(), step = GRID * z
  el.style.backgroundSize = grid.value && step >= 6 ? `${step}px ${step}px` : ''
  el.style.backgroundPosition = grid.value ? `${p.x - step / 2}px ${p.y - step / 2}px` : '' // a tile's dot is in its middle: shift it onto the crossing
}
function snap(node) {
  const nodes = node.isParent() ? node.descendants().filter((n) => !n.isParent()) : node
  const dx = (v) => Math.round(v / GRID) * GRID - v
  const ref = node.isParent() ? nodes[0]?.position() : node.position()
  if (!ref) return
  const shift = { x: dx(ref.x), y: dx(ref.y) } // a box keeps its guests' arrangement: all move by the same amount
  nodes.forEach((n) => n.position({ x: n.position('x') + shift.x, y: n.position('y') + shift.y }))
}
watch(grid, paintGrid)
function fit() { cy.value?.animate({ fit: { padding: 30 }, duration: 200 }) }

// ------------------------------------------------------------- the side panel
const edgesOf = computed(() => {
  const s = sel.value
  if (!s || !data.value) return []
  const mine = new Set([s.id, ...data.value.nodes.filter((n) => n.type === 'port' && n.host === (s.host || s.name)).map((n) => n.id)])
  return data.value.edges.filter((e) => e.kind !== 'stack' && (mine.has(e.source) || mine.has(e.target)))
})
const peer = (e) => {
  const me = sel.value.name || sel.value.host
  if (e.kind === 'address') {
    if (sel.value.type === 'network') return { host: e.a_host, mine: '', theirs: `${e.a_port ? `${e.a_port} ` : ''}${e.label}` }
    return { net: e.target.slice(2), mine: e.a_port, theirs: e.label }
  }
  if (e.a_host) return e.a_host === me ? { host: e.b_host, mine: e.a_port, theirs: e.b_port } : { host: e.a_host, mine: e.b_port, theirs: e.a_port }
  const other = e.source === `h:${me}` ? e.target : e.source
  const n = data.value.nodes.find((x) => x.id === other)
  return { host: n?.host || n?.name || '?', mine: e.a_port, theirs: e.b_port }
}
async function removeCable(e) {
  if (!(await confirm({ title: 'Remove this cable?', body: `${e.a_host} ${e.a_port} – ${e.b_host} ${e.b_port}. Both hosts get a history entry, so it can be reverted.`, action: 'Remove', danger: true }))) return
  try {
    await api.del(`/api/topology/cables/${e.id.slice(2)}`)
    toast('Cable removed')
    sel.value = null
    query.refresh()
  } catch (x) { toast(x.message, 'error') }
}

// a cable picked on the map: its kind, speed and notes can change
const edgeForm = ref(null)
const EDGE_KEYS = { medium: 'medium', speed: 'speed', notes: 'notes', label: 'cable_label', length: 'length', colour: 'cable_colour' } // form field: edge data
watch(sel, (s) => { edgeForm.value = s?.type === 'edge' && s.kind === 'cable' ? Object.fromEntries(Object.entries(EDGE_KEYS).map(([k, d]) => [k, s[d] || ''])) : null })
const edgeDirty = computed(() => edgeForm.value && Object.entries(EDGE_KEYS).some(([k, d]) => (edgeForm.value[k] || '') !== (sel.value[d] || '')))
async function saveEdge() {
  try {
    await api.put(`/api/topology/cables/${sel.value.link}`, edgeForm.value)
    toast('Cable changed')
    sel.value = null
    query.refresh()
  } catch (x) { toast(x.errors?.map((e) => e.message).join('; ') || x.message, 'error') }
}
const hostName = (id) => { const n = data.value?.nodes.find((x) => x.id === id); return n ? n.host || n.name : '' }

// ----------------------------------------------------------- drawing a cable
const wiring = ref(null) // null, or {a: {host, port}} once the first end is picked
const cableDlg = ref(null)
const busy = ref(false)
function startWiring() { sel.value = null; wiring.value = { a: null } }
function stopWiring() { wiring.value = null; cy.value?.nodes().removeClass('from') }
function endOf(n) { return n.type === 'port' ? { host: n.host, port: n.label } : n.type === 'box' ? { host: n.host, port: '' } : { host: n.name, port: '' } }
function tapNode(node) {
  const n = node.data()
  if (!wiring.value) { sel.value = n.type === 'port' ? null : n; return }
  if (n.type === 'network') return
  if (!wiring.value.a) { wiring.value = { a: endOf(n) }; node.addClass('from'); return }
  const b = endOf(n)
  if (b.host === wiring.value.a.host && b.port && b.port === wiring.value.a.port) { toast('Pick another port for the other end', 'error'); return }
  openCable(wiring.value.a, b) // the same host twice: a cable between two of its ports
  stopWiring()
}
async function openCable(a, b) {
  const [ha, hb] = await Promise.all([api.get(`/api/hosts/${encodeURIComponent(a.host)}`), api.get(`/api/hosts/${encodeURIComponent(b.host)}`)])
  const ea = cableEnd(a, ha)
  const eb = cableEnd(b, hb, a.host === b.host ? ea.port : '')
  cableDlg.value = { a: ea, b: eb, medium: 'copper', speed: '1G', notes: '', label: '', length: '', colour: '', errors: {}, same: a.host === b.host }
}
// One end of the new cable. pick: 'l:<label>' (a named interface), 'u:<index>' (an unnamed one, named
// here), or 'new' (a new port); port: the name the cable plugs into.
function cableEnd(end, h, not = '') {
  const opts = choices(h)
  // a free named port first; a host without cables yet (a VM, say) names its interface; a switch gets a new port
  const unnamed = opts.find((o) => o.value.startsWith('u:'))
  const first = end.port ? `l:${end.port}` : (opts.find((o) => o.free && o.value.startsWith('l:') && o.value !== `l:${not}`) || (!h.cables.length && unnamed) || opts.at(-1)).value
  const s = { host: end.host, h, pick: first, port: end.port || '' }
  setPick(s, first)
  return s
}
function choices(h) {
  const cabled = (label) => h.cables.find((c) => c.port === label && c.medium !== 'virtual')
  const out = []
  h.interfaces.forEach((i, n) => {
    const where = [i.network, i.ip4 || i.ip6].filter(Boolean).join(' ')
    if (i.label) {
      const c = cabled(i.label)
      out.push({ value: `l:${i.label}`, text: `${i.label}${where ? `  (${where})` : ''}${c ? ` — cabled to ${c.peer}` : ''}`, free: !c })
    } else {
      out.push({ value: `u:${n}`, text: `unnamed interface${where ? ` in ${where}` : ''} — name it`, free: true })
    }
  })
  out.push({ value: 'new', text: 'New port…' })
  return out
}
function setPick(s, v) {
  s.pick = v
  if (v.startsWith('l:')) s.port = v.slice(2)
  else if (!s.port || s.h.interfaces.some((i) => i.label === s.port)) s.port = suggest(s.h) // keep a name typed before
}
function suggest(h) { // eth0, eth1 … the first one it doesn't have
  for (let n = 0; ; n++) if (!h.interfaces.some((i) => i.label === `eth${n}`)) return `eth${n}`
}
const isNew = (s) => s.pick === 'new'
const taken = (s) => s.pick.startsWith('l:') && s.h.cables.find((c) => c.port === s.port && c.medium !== 'virtual')
async function saveCable() {
  const d = cableDlg.value
  busy.value = true
  try {
    const end = (x) => ({ host: x.host, port: x.port, ...(x.pick.startsWith('u:') ? { iface: Number(x.pick.slice(2)) } : {}) })
    await api.post('/api/topology/cables', { a: end(d.a), b: end(d.b), medium: d.medium, speed: d.speed, notes: d.notes, label: d.label, length: d.length, colour: d.colour })
    toast(`Cable ${d.a.host} ${d.a.port} – ${d.b.host} ${d.b.port} added`)
    cableDlg.value = null
    query.refresh()
  } catch (e) {
    d.errors = Object.fromEntries((e.errors || []).map((x) => [x.field, x.message]))
    if (!e.errors?.length) toast(e.message, 'error')
  } finally { busy.value = false }
}
const errFor = (side) => {
  const e = cableDlg.value?.errors || {}
  if (side === 'label' || side === 'length') return Object.entries(e).find(([k]) => k.endsWith(`.${side}`) && k.startsWith('cables['))?.[1] || ''
  return e[`${side}.port`] || e[`${side}.host`] || Object.entries(e).filter(([k]) => !['a.port', 'b.port', 'a.host', 'b.host'].includes(k) && !/\.(label|length)$/.test(k)).map(([k, v]) => `${k}: ${v}`).join('; ')
}

function setView(v) { view.value = v; sel.value = null; stopWiring() }
watch(data, () => nextTick(draw))
watch([lonely, hiddenNets], () => draw(), { deep: true })
watch(shownTheme, () => nextTick(draw)) // colours come from the theme
watch(q, highlight)
watch([zone, view, current], ([z, v, cur]) => router.replace({ query: { ...(z ? { zone: z } : {}), ...(v === 'l3' ? { view: v } : {}), ...(cur ? { v: cur.uid } : {}) } }))
onMounted(() => { if (data.value) draw() })
onBeforeUnmount(() => cy.value?.destroy())
</script>

<template>
  <div class="page wide">
    <div class="page-head">
      <h1>Topology</h1>
      <span v-if="stats && data.view === 'l3'" class="sub">{{ stats.networks }} networks · {{ stats.hosts }} hosts · {{ stats.routers }} in several networks</span>
      <span v-else-if="stats" class="sub">{{ stats.hosts }} hosts · {{ stats.boxes }} hypervisors · {{ stats.cables }} cable{{ stats.cables === 1 ? '' : 's' }} · {{ stats.internal }} internal link{{ stats.internal === 1 ? '' : 's' }}</span>
      <span class="grow"></span>
      <input v-model="q" type="search" placeholder="Find a host, address or network" style="max-width: 260px" aria-label="Find in the picture">
    </div>
    <nav class="tabs">
      <a href="#" :class="{ 'router-link-active': view === 'physical' }" :aria-current="view === 'physical' ? 'page' : undefined" @click.prevent="setView('physical')">Physical</a>
      <a href="#" :class="{ 'router-link-active': view === 'l3' }" :aria-current="view === 'l3' ? 'page' : undefined" @click.prevent="setView('l3')">Networks (L3 / VLANs)</a>
    </nav>
    <div class="filters">
      <FilterSelect v-model="zone" label="Zone" :options="zones" />
      <label v-if="view === 'physical'" class="check small"><input v-model="lonely" type="checkbox"> Hosts without cables{{ stats?.lonely ? ` (${stats.lonely})` : '' }}</label>
      <label class="check small" title="A dot grid; what you drop snaps to it"><input v-model="grid" type="checkbox"> Grid</label>
      <span class="grow"></span>
      <button v-if="view === 'physical' && canArrange" class="btn small" :class="{ primary: wiring }" title="Draw a cable: click both ends" @click="wiring ? stopWiring() : startWiring()">
        <Cable :size="14" /> {{ wiring ? 'Cancel cable' : 'Cable' }}</button>
      <select class="viewpick small auto" :value="current?.uid || ''" aria-label="Saved view" @change="pickView($event.target.value)">
        <option value="">{{ views.length ? 'Saved views…' : 'No saved views' }}</option>
        <option v-for="v in views" :key="v.uid" :value="v.uid">{{ v.name }}</option></select>
      <button v-if="canArrange" class="btn small" title="Save what you see as a view: picture, zone, networks, options and zoom" @click="viewDlg = { name: current?.name || '', error: '' }"><Save :size="14" /> View</button>
      <button class="btn small" title="Fit everything in view" @click="fit"><LocateFixed :size="14" /> Fit</button>
      <span v-if="savedNote" class="small faint" role="status">{{ savedNote }}</span>
      <MenuButton :icon="MoreHorizontal" title="More" :items="[
        { label: 'Download as PNG', icon: Download, run: () => exportAs('png') },
        { label: 'Download as SVG', icon: Download, run: () => exportAs('svg') },
        { label: 'Arrange again', icon: RefreshCw, run: arrange },
      ]" />
    </div>
    <div v-if="view === 'l3' && allNets.length" class="netchips" role="group" aria-label="Networks shown">
      <span class="small faint">Networks</span>
      <button v-for="n in allNets" :key="n" type="button" class="chip" :class="{ off: hiddenNets.includes(n) }" :aria-pressed="!hiddenNets.includes(n)" @click="toggleNet(n)">{{ n }}</button>
      <button v-if="hiddenNets.length" type="button" class="btn small ghost" @click="hiddenNets = []">all</button>
    </div>
    <div class="canvas-wrap" :class="{ wiring, grid }">
      <div v-if="view === 'physical' && speedsUsed.any" class="legend small onmap">
        <div class="seg mini" role="radiogroup" aria-label="Colour cables by">
          <button type="button" role="radio" :aria-checked="lines === 'cable'" :class="{ on: lines === 'cable' }" @click="lines = 'cable'">Cable colour</button>
          <button type="button" role="radio" :aria-checked="lines === 'speed'" :class="{ on: lines === 'speed' }" @click="lines = 'speed'">Speed</button>
        </div>
        <template v-if="lines === 'speed'">
          <span v-for="x in speedsUsed.steps" :key="x.label"><i :style="{ background: x.colour }"></i>{{ x.label }}</span>
          <span v-if="speedsUsed.unknown"><i class="none"></i>no speed</span>
        </template>
        <template v-else>
          <span v-for="x in speedsUsed.colours" :key="x.label"><i class="cc" :style="{ background: x.colour }"></i>{{ x.label }}</span>
          <span v-if="speedsUsed.uncoloured"><i class="none"></i>no colour set</span>
          <span class="faint">thicker is faster</span>
        </template>
      </div>
      <div ref="box" class="canvas" role="img" :aria-label="view === 'l3' ? 'Networks of the lab and the hosts in them' : 'Topology of the lab: hosts, hypervisors with their guests, and cables'"></div>
      <div v-if="wiring" class="wirehint" role="status">{{ wiring.a ? `From ${wiring.a.host}${wiring.a.port ? ` ${wiring.a.port}` : ''}: now click the other end (the same host again for a cable between two of its ports)` : 'Click the first end: a host, or a port inside a hypervisor' }}
        <button class="btn small ghost" @click="stopWiring">Cancel</button></div>
      <div v-if="data && view === 'physical' && !data.edges.length && !data.nodes.some((n) => n.type === 'box')" class="emptyhint">
        Nothing to draw yet. Draw a cable with <b>Cable</b>, add one on a host's Network tab (an <b>internal</b> one joins a VM to its hypervisor's bridge),
        or set what a host runs on: hypervisors become boxes with their guests inside.
      </div>
      <div v-else-if="data && view === 'l3' && !data.nodes.length" class="emptyhint">No host has an address in a network yet.</div>
      <div v-if="hover && !sel" class="hovercard" :class="{ up: hover.up }" :style="{ left: `${hover.x}px`, top: `${hover.y}px` }" role="tooltip">
        <b>{{ hover.n.fun || hover.n.name || hover.n.host }}</b> <span v-if="hover.n.fun" class="mono small muted">{{ hover.n.name || hover.n.host }}</span>
        <div v-if="hover.n.function" class="small">{{ hover.n.function }}</div>
        <div class="row small" style="gap: 6px; margin-top: 4px"><ZoneChip v-if="hover.n.zone" :zone="hover.n.zone" />
          <span class="status" :class="`s-${hover.n.status}`">{{ hover.n.status }}</span><span v-if="hover.n.address" class="mono">{{ hover.n.address }}</span></div>
      </div>
      <aside v-if="sel && sel.type === 'edge'" class="panel">
        <div class="ph"><Cable :size="16" /><b>{{ sel.kind === 'address' ? 'Address' : sel.kind === 'vip' ? 'Shared address' : sel.kind === 'internal' ? 'Internal link' : 'Cable' }}</b>
          <span class="grow"></span><button class="btn icon" aria-label="Close" @click="sel = null"><X :size="15" /></button></div>
        <div class="ends small">
          <div><RouterLink :to="`/hosts/${sel.a_host || hostName(sel.source)}?tab=network`">{{ sel.a_host || hostName(sel.source) }}</RouterLink>
            <span class="mono">{{ sel.a_port }}</span></div>
          <div class="faint">{{ sel.kind === 'address' ? 'in' : sel.kind === 'vip' ? 'holds' : '—' }}</div>
          <div v-if="sel.kind === 'address'"><RouterLink :to="`/networks/${sel.target.slice(2)}`">{{ sel.target.slice(2) }}</RouterLink> <span class="mono">{{ sel.label }}</span></div>
          <div v-else><RouterLink :to="`/hosts/${sel.b_host || hostName(sel.target)}?tab=network`">{{ sel.b_host || hostName(sel.target) }}</RouterLink>
            <span class="mono">{{ sel.b_port }}</span></div>
        </div>
        <p v-if="sel.auto" class="small muted">Guessed: the guest's interface has no “sits on” set, so it's drawn to {{ sel.b_host }}'s bridge in the same network. Set it on the guest's Network tab to make it exact.</p>
        <p v-if="sel.kind === 'vip'" class="small muted">{{ sel.label || 'shared' }}: the VIP's members are set on the VIP.</p>
        <template v-if="edgeForm">
          <div v-if="canArrange" class="g2 mt-3">
            <Field label="Medium"><select v-model="edgeForm.medium"><option value="">—</option><option v-for="m in MEDIA" :key="m" :value="m">{{ m }}</option></select></Field>
            <Field label="Speed"><input v-model.trim="edgeForm.speed" list="cable-speeds-t" placeholder="1G"></Field>
          </div>
          <Field v-if="canArrange" label="Notes"><input v-model="edgeForm.notes" placeholder="patch panel 12"></Field>
          <CableExtras v-if="canArrange" v-model:cable="edgeForm" compact />
          <div v-else class="small mt-2">{{ [sel.cable_label, sel.medium, sel.speed, sel.length ? `${sel.length} m` : '', sel.cable_colour, sel.notes].filter(Boolean).join(' · ') || 'No details.' }}</div>
          <div v-if="canArrange" class="row" style="gap: 8px; margin-top: 10px">
            <button class="btn small danger" @click="removeCable(sel)"><Trash2 :size="13" /> Remove</button><span class="grow"></span>
            <button v-if="sel.kind === 'cable'" class="btn small" @click="tracing = { host: sel.a_host, port: sel.a_port }"><Route :size="13" /> Trace</button>
            <button class="btn small primary" :disabled="!edgeDirty" @click="saveEdge">Save</button></div>
        </template>
      </aside>
      <aside v-else-if="sel" class="panel">
        <div class="ph"><Server :size="16" /><b>{{ sel.label }}</b><span class="grow"></span>
          <button class="btn icon" aria-label="Close" @click="sel = null"><X :size="15" /></button></div>
        <template v-if="sel.type === 'network'">
          <div class="mono small muted">{{ [sel.vlan != null ? `VLAN ${sel.vlan}` : '', sel.prefix].filter(Boolean).join(' · ') }}</div>
          <div class="row small" style="gap: 8px; margin-top: 8px"><ZoneChip v-if="sel.zone" :zone="sel.zone" /><span v-if="sel.gateway" class="mono">gw {{ sel.gateway }}</span></div>
        </template>
        <template v-else>
          <div class="mono small muted">{{ sel.name || sel.host }}</div>
          <div v-if="sel.function" class="small mt-2">{{ sel.function }}</div>
          <div class="row small" style="gap: 8px; margin-top: 8px"><ZoneChip v-if="sel.zone" :zone="sel.zone" />
            <span class="status" :class="`s-${sel.status}`">{{ sel.status }}</span><span v-if="sel.address" class="mono">{{ sel.address }}</span></div>
        </template>
        <div v-if="edgesOf.length" class="links">
          <div v-for="e in edgesOf" :key="e.id" class="small lk">
            <template v-if="peer(e).net"><span class="mono">{{ peer(e).mine || '·' }}</span><span class="faint"> in </span>
              <RouterLink :to="`/networks/${peer(e).net}`">{{ peer(e).net }}</RouterLink> <span class="mono faint">{{ peer(e).theirs }}</span></template>
            <template v-else>
              <i v-if="e.kind === 'cable'" class="sw" :style="{ background: speedStep(e.speed)?.colour || 'var(--line-strong)' }"></i>
              <span class="mono">{{ peer(e).mine || '·' }}</span>
              <span :class="e.kind === 'internal' ? 'c-accent' : 'faint'">{{ e.kind === 'internal' ? ' ⇢ ' : e.kind === 'address' ? ' ' : ' — ' }}</span>
              <RouterLink :to="`/hosts/${peer(e).host}`">{{ peer(e).host }}</RouterLink> <span class="mono faint">{{ peer(e).theirs }}</span>
              <span v-if="e.speed" class="faint"> {{ e.speed }}</span>
              <span class="grow"></span>
              <button v-if="canArrange && e.kind === 'cable'" class="btn icon tiny" :aria-label="`Remove the cable to ${peer(e).host}`" title="Remove this cable" @click="removeCable(e)"><Trash2 :size="13" /></button>
            </template>
          </div>
        </div>
        <p v-else class="small faint">{{ view === 'l3' ? 'No addresses.' : 'No cables.' }}</p>
        <RouterLink class="btn small mt-3" :to="sel.type === 'network' ? `/networks/${sel.uid || sel.name}` : `/hosts/${sel.uid || sel.name || sel.host}?tab=network`">
          {{ sel.type === 'network' ? 'Open the network' : 'Open the host' }}</RouterLink>
      </aside>
    </div>
    <p v-if="view === 'physical'" class="hint">Drag to arrange: the map is shared, everyone sees the same{{ data?.layout_by ? ` (last moved by ${data.layout_by})` : '' }}. Scroll to zoom; point at a host for a summary, click a line for its ends and details, double-click a host to open it.
      A host's fun name is a label of its own: drag it to wherever it reads best. Cables are drawn in their own colour (or by speed, switch it on the legend) and thicker when faster;
      dashed lines are internal links inside a hypervisor, dotted ones how its interfaces are stacked.</p>
    <p v-else class="hint">Every host joined to the networks it has an address in; hosts in several networks (routers, firewalls, multi-homed servers) are diamonds.
      Hover a line for the address. The arrangement is shared and kept apart from the physical view.</p>

    <Modal v-if="viewDlg" title="Save this view" @close="viewDlg = null">
      <p class="small muted m-0">Keeps the picture ({{ view === 'l3' ? 'networks' : 'physical' }}), the zone, hidden networks, options and where you've zoomed to.
        Everyone who sees the topology can open it; the link opens it straight away.</p>
      <Field label="Name" :error="viewDlg.error"><input v-model.trim="viewDlg.name" placeholder="Rack room, Kube networks …" @keydown.enter="saveView(!current)"></Field>
      <template #foot>
        <button v-if="current" class="btn danger left" @click="viewDlg = null; deleteView()">Delete {{ current.name }}</button>
        <button class="btn" @click="viewDlg = null">Cancel</button>
        <button v-if="current" class="btn" :disabled="!viewDlg.name" @click="saveView(true)">Save as new</button>
        <button class="btn primary" :disabled="!viewDlg.name" @click="saveView(!current)">{{ current ? `Update ${current.name}` : 'Save' }}</button>
      </template>
    </Modal>
    <Modal v-if="tracing" :title="`Trace ${tracing.host} ${tracing.port}`" @close="tracing = null"><CableTrace :host="tracing.host" :port="tracing.port" /></Modal>
    <datalist id="cable-speeds-t"><option v-for="x in SPEEDS" :key="x" :value="x" /></datalist>
    <Modal v-if="cableDlg" :title="cableDlg.same ? `A cable inside ${cableDlg.a.host}` : 'New cable'" wide :busy="busy" @close="cableDlg = null">
      <div class="g2">
        <div v-for="side in ['a', 'b']" :key="side" class="end">
          <div class="small faint">{{ side === 'a' ? 'From' : 'To' }}</div>
          <b class="mono mb-1">{{ cableDlg[side].host }}</b>
          <Field label="Interface" :error="errFor(side)">
            <select :value="cableDlg[side].pick" :aria-label="`Interface on ${cableDlg[side].host}`" @change="setPick(cableDlg[side], $event.target.value)">
              <option v-for="o in choices(cableDlg[side].h)" :key="o.value" :value="o.value">{{ o.text }}</option></select>
          </Field>
          <Field v-if="!cableDlg[side].pick.startsWith('l:')" :label="isNew(cableDlg[side]) ? 'Name of the new port' : 'Its name'">
            <input v-model.trim="cableDlg[side].port" class="mono" placeholder="eth0" :aria-label="`Port name on ${cableDlg[side].host}`"></Field>
          <p v-if="isNew(cableDlg[side])" class="small c-accent m-0">Added to {{ cableDlg[side].host }} as a new interface.</p>
          <p v-else-if="taken(cableDlg[side])" class="small c-yellow m-0 mt-1">Has a cable to {{ taken(cableDlg[side]).peer }} {{ taken(cableDlg[side]).peer_port }}; that one goes.</p>
        </div>
      </div>
      <div class="g2">
        <Field label="Medium"><select v-model="cableDlg.medium"><option v-for="m in MEDIA" :key="m" :value="m">{{ m }}</option></select></Field>
        <Field label="Speed"><input v-model.trim="cableDlg.speed" list="cable-speeds-t" placeholder="1G"></Field>
      </div>
      <Field label="Notes"><input v-model="cableDlg.notes" placeholder="patch panel 12"></Field>
      <CableExtras v-model:cable="cableDlg" :errors="{ label: errFor('label'), length: errFor('length') }" />
      <template #foot>
        <span class="small faint left">Saved on both hosts, with history</span>
        <button class="btn" @click="cableDlg = null">Cancel</button>
        <button class="btn primary" :disabled="!cableDlg.a.port || !cableDlg.b.port || busy" @click="saveCable">Add cable</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.page.wide { max-width: none; }
.canvas-wrap { position: relative; height: calc(100vh - 250px); min-height: 420px; border: 1px solid var(--line); border-radius: var(--r-box); background: var(--bg); overflow: hidden; }
.canvas-wrap.wiring { border-color: var(--accent); }
.canvas { position: absolute; inset: 0; }
.canvas-wrap.grid { background-image: radial-gradient(circle, var(--line-strong) 1.25px, transparent 1.6px); }
.hovercard { position: absolute; transform: translateX(-50%); background: var(--card); border: 1px solid var(--line-strong); border-radius: var(--r-ctl);
  padding: 8px 10px; box-shadow: 0 8px 20px var(--shadow); pointer-events: none; max-width: 280px; z-index: 2; }
.hovercard.up { transform: translate(-50%, -100%); }
.ends { display: grid; gap: 2px; margin-top: 8px; }
.ends .mono { margin-left: 4px; }
.viewpick { max-width: 150px; }
.netchips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 10px; }
.chip { font: inherit; font-size: 12px; font-family: var(--mono, 'IBM Plex Mono', monospace); border: 1px solid var(--line-strong); background: var(--card);
  color: var(--ink); border-radius: 999px; padding: 2px 10px; cursor: pointer; }
.chip.off { color: var(--faint); background: none; text-decoration: line-through; }
.panel { position: absolute; top: 12px; right: 12px; width: 320px; max-height: calc(100% - 24px); overflow: auto; background: var(--card);
  border: 1px solid var(--line-strong); border-radius: var(--r-box); padding: 12px 14px; box-shadow: 0 12px 30px var(--shadow); }
.ph { display: flex; align-items: center; gap: 8px; }
.links { margin-top: 10px; display: flex; flex-direction: column; gap: 4px; border-top: 1px solid var(--line); padding-top: 8px; }
.lk { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.sw { width: 10px; height: 4px; border-radius: 2px; display: inline-block; margin-right: 2px; }
.tiny { padding: 2px; }
.emptyhint { position: absolute; inset: 0; display: grid; place-items: center; text-align: center; padding: 40px; color: var(--muted); max-width: 60ch; margin: auto; pointer-events: none; }
.wirehint { position: absolute; top: 12px; left: 12px; background: var(--card); border: 1px solid var(--accent); border-radius: var(--r-ctl);
  padding: 6px 6px 6px 12px; display: flex; gap: 10px; align-items: center; font-size: 13px; box-shadow: 0 8px 20px var(--shadow); }
.legend.onmap { position: absolute; left: 10px; bottom: 10px; z-index: 1; background: var(--card); border: 1px solid var(--line); border-radius: var(--r-ctl); padding: 4px 10px 4px 4px;
  align-items: center; max-width: calc(100% - 20px); }
.seg.mini button { font-size: 12px; padding: 2px 9px; }
.legend i.cc { box-shadow: 0 0 0 1px var(--line-strong); }
.legend { display: inline-flex; gap: 10px; flex-wrap: wrap; color: var(--muted); }
.legend span { display: inline-flex; align-items: center; gap: 4px; }
.legend i { width: 16px; height: 4px; border-radius: 2px; display: inline-block; }
.legend i.none { background: var(--line-strong); }
.end { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.c-accent { color: var(--accent-soft); }
@media (max-width: 700px) { .panel { left: 12px; right: 12px; width: auto; top: auto; bottom: 12px; max-height: 50%; } }
</style>
