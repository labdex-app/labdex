<script setup>
import { Bookmark, Pencil, Plus, Tag, Trash2, X } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { confirm } from '../confirm'
import { prefetch, useQuery } from '../query'
import ChipInput from '../components/ChipInput.vue'
import Field from '../components/Field.vue'
import HostPicker from '../components/HostPicker.vue'
import MenuButton from '../components/MenuButton.vue'
import Modal from '../components/Modal.vue'
import Copy from '../components/Copy.vue'
import FilterSelect from '../components/FilterSelect.vue'
import HostIcon from '../components/HostIcon.vue'
import TagChip from '../components/TagChip.vue'
import ZoneChip from '../components/ZoneChip.vue'
import { can, changed, state, tagColour, toast } from '../store'

const route = useRoute()
const router = useRouter()
const query = useQuery('/api/hosts', { onError: (e) => toast(e.message, 'error') })
const hosts = computed(() => query.data.value || [])
const loading = computed(() => query.loading.value && !query.data.value)
const q = ref(String(route.query.q || ''))
const filter = ref({ zone: route.query.zone || null, cat: route.query.cat || null, status: route.query.status || null, tag: route.query.tag || null })
// Physical, virtual (runs on something, or its name says vm/ct/lxc/vps) and VIPs
const VIRTUAL_TYPES = new Set(['vm', 'ct', 'lxc', 'vps', 'pod'])
const TABS = ['physical', 'virtual', 'vip']
const TAB_LABEL = { physical: 'Physical', virtual: 'Virtual', vip: 'VIPs' }
const tabOf = (h) => (h.kind === 'vip' ? 'vip' : h.runs_on || (h.name_ok && VIRTUAL_TYPES.has(h.name.split('-')[1])) ? 'virtual' : 'physical')
const fromQuery = (k) => (TABS.includes(k) ? k : k === 'device' ? 'physical' : 'physical')
const tab = ref(fromQuery(route.query.kind))
const inTab = computed(() => hosts.value.filter((h) => tabOf(h) === tab.value))
const groupBy = ref(String(route.query.group || ''))
const GROUPS = [{ value: 'tag', label: 'tag' }, { value: 'zone', label: 'zone' }, { value: 'runs_on', label: 'runs on' }, { value: 'cat', label: 'category' }, { value: 'status', label: 'status' }]
function setTab(t) { tab.value = t; syncQuery() }
const sort = ref({ key: 'name', dir: 1 })
const PAGE = 200
const shown = ref(PAGE)

// The page stays alive between visits; links from the sidebar change the query.
watch(() => route.query, (rq) => {
  if (route.path !== '/hosts') return
  q.value = String(rq.q || '')
  filter.value = { zone: rq.zone || null, cat: rq.cat || null, status: rq.status || null, tag: rq.tag || null }
  tab.value = fromQuery(rq.kind)
  groupBy.value = String(rq.group || '')
})
watch([q, filter, sort, tab, groupBy], () => { shown.value = PAGE }, { deep: true })

// One lower-cased search text per host (values only, not key names), built once per answer, not per keystroke.
const values = (v) => (v == null || typeof v === 'boolean' ? '' : typeof v === 'object' ? Object.values(v).map(values).join(' ') : String(v))
const haystack = computed(() => new Map(hosts.value.map((h) => [h, values(h).toLowerCase()])))
const hover = (h) => prefetch(`/api/hosts/${h.uid || encodeURIComponent(h.name)}`)

const cat = (h) => (h.name_ok ? h.name.split('-')[0] : 'unnamed')
const uniq = (arr) => [...new Set(arr.filter(Boolean))]

// monitoring sources that report on any host get a column each
const sources = computed(() => [...new Set(hosts.value.flatMap((h) => Object.keys(h.live_sources || {})))].sort())
const LIVE = { up: 'up', down: 'down', unknown: 'no data' }

const facets = computed(() => [
  { key: 'zone', label: 'Zone', values: state.meta.zones.map((z) => z.code).filter((z) => inTab.value.some((h) => h.zone === z)) },
  { key: 'cat', label: 'Category', values: uniq(inTab.value.map(cat)) },
  { key: 'status', label: 'Status', values: state.meta.statuses.filter((s) => inTab.value.some((h) => h.status === s)) },
  { key: 'tag', label: 'Tag', values: uniq(inTab.value.flatMap((h) => h.tags || [])).sort() },
])

// The search box understands tag:backup zone:DC_TC status:active on:srv-pm-pve-01 net:srv cat:srv
// (quote values with spaces); everything else is plain text. Several tag: terms all have to match.
const TERMS = { tag: (h, v) => (h.tags || []).includes(v), zone: (h, v) => h.zone.toLowerCase() === v, status: (h, v) => h.status === v,
  on: (h, v) => (h.runs_on || '') === v, net: (h, v) => h.interfaces.some((i) => i.network === v), cat: (h, v) => cat(h) === v }
const parsed = computed(() => {
  const terms = []
  const text = q.value.replace(/\b(tag|zone|status|on|net|cat):("([^"]*)"|\S+)/gi, (_, k, raw, quoted) => {
    terms.push([k.toLowerCase(), (quoted ?? raw).toLowerCase()])
    return ' '
  })
  return { terms, needle: text.trim().toLowerCase() }
})
const searching = computed(() => !!(parsed.value.needle || parsed.value.terms.length || Object.values(filter.value).some(Boolean)))
function matches(h) {
  const { terms, needle } = parsed.value
  const f = filter.value
  return (!f.zone || h.zone === f.zone) &&
    (!f.tag || (h.tags || []).includes(f.tag)) &&
    (!f.cat || cat(h) === f.cat) &&
    (!f.status || h.status === f.status) &&
    terms.every(([k, v]) => TERMS[k](h, v)) &&
    (!needle || haystack.value.get(h).includes(needle))
}
// per tab: every host, or while searching, the hosts that match (so a hit in another tab shows)
const counts = computed(() => Object.fromEntries(TABS.map((t) => [t, hosts.value.filter((h) => tabOf(h) === t && (!searching.value || matches(h))).length])))
const elsewhere = computed(() => TABS.filter((t) => t !== tab.value && counts.value[t]))
const visible = computed(() => {
  const rows = inTab.value.filter(matches)
  const { key, dir } = sort.value
  const val = (h) => (key === 'name' ? (h.fun || h.name) : key === 'function' ? h.function : h[key]) || ''
  return [...rows].sort((a, b) => val(a).localeCompare(val(b)) * dir)
})

// Grouped rows: a heading per group, the hosts below it (a host with two tags is in both groups)
const keysOf = (h) => {
  const g = groupBy.value
  if (g === 'tag') return h.tags?.length ? h.tags : ['']
  if (g === 'cat') return [cat(h)]
  return [h[g] || '']
}
const rows = computed(() => {
  const list = visible.value.slice(0, shown.value)
  const index = new Map(visible.value.map((h, i) => [h.name, i]))
  if (!groupBy.value) return list.map((h) => ({ h, idx: index.get(h.name), key: h.name }))
  const groups = new Map()
  for (const h of list) for (const k of keysOf(h)) { if (!groups.has(k)) groups.set(k, []); groups.get(k).push(h) }
  const keys = [...groups.keys()].sort((a, b) => (a === '') - (b === '') || a.localeCompare(b))
  return keys.flatMap((k) => [{ head: true, key: `g:${k}`, label: k, n: groups.get(k).length },
    ...groups.get(k).map((h) => ({ h, idx: index.get(h.name), key: `${k}:${h.name}` }))])
})
const NOGROUP = { tag: 'no tag', zone: 'no zone', runs_on: 'physical (runs on nothing)', cat: 'unnamed', status: '—' }
function setGroup(v) { groupBy.value = v || ''; syncQuery() }
function addTerm(k, v) { // a click on a tag chip filters by it
  const t = `${k}:${/\s/.test(v) ? `"${v}"` : v}`
  if (!q.value.includes(t)) q.value = `${q.value.trim()} ${t}`.trim()
  syncQuery()
}

function sortBy(key) {
  sort.value = { key, dir: sort.value.key === key ? -sort.value.dir : 1 }
}

function syncQuery() {
  const query = Object.fromEntries(Object.entries({ q: q.value, ...filter.value, kind: tab.value === 'physical' ? null : tab.value,
    group: groupBy.value || null }).filter(([, v]) => v))
  router.replace({ query })
}

// ------------------------------------------------------------ saved searches
const saved = useQuery('/api/searches')
const searches = computed(() => saved.data.value?.searches || [])
const current = computed(() => Object.fromEntries(Object.entries({ q: q.value.trim(), ...filter.value, kind: tab.value === 'physical' ? null : tab.value,
  group: groupBy.value || null }).filter(([, v]) => v)))
const sameQuery = (a, b) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort())
const activeSearch = computed(() => searches.value.find((s) => sameQuery(s.query, current.value)))
const canSave = computed(() => saved.data.value?.can_own && Object.keys(current.value).length && !activeSearch.value)
const searchItems = computed(() => [
  ...searches.value.map((sr) => ({ label: `${sr.name}${sr.shared ? ' (shared)' : ''}`, run: () => applySearch(sr) })),
  ...(activeSearch.value?.can_edit ? [{ label: `Delete “${activeSearch.value.name}”`, danger: true, run: () => deleteSearch(activeSearch.value) }] : []),
  ...(canSave.value ? [{ label: 'Save this search …', run: openSave }] : []),
])
const saveDlg = ref(null)
function openSave() { saveDlg.value = { name: '', shared: false, error: '' } }
function applySearch(s) {
  q.value = s.query.q || ''
  filter.value = { zone: s.query.zone || null, cat: s.query.cat || null, status: s.query.status || null, tag: s.query.tag || null }
  tab.value = fromQuery(s.query.kind)
  groupBy.value = s.query.group || ''
  syncQuery()
}
async function saveSearch() {
  busy.value = true
  saveDlg.value.error = ''
  try {
    await api.post('/api/searches', { name: saveDlg.value.name, shared: saveDlg.value.shared, query: current.value })
    saveDlg.value = null
    await saved.refresh()
    toast('Search saved')
  } catch (e) {
    saveDlg.value.error = e instanceof ApiError && e.fields.name ? e.fields.name : e.fields?.query || e.message
  } finally { busy.value = false }
}
async function deleteSearch(s) {
  if (!(await confirm({ title: `Delete the search “${s.name}”?`, body: s.shared ? 'It disappears for everyone.' : '', action: 'Delete', danger: true }))) return
  try {
    await api.del(`/api/searches/${s.uid}`)
    await saved.refresh()
  } catch (e) { toast(e.message, 'error') }
}

// ------------------------------------------------------------ selection and bulk changes
const picked = ref(new Set())
const editable = can('host', 'edit')
let lastIdx = -1
function pick(h, idx, e) {
  const s = new Set(picked.value)
  const rows = visible.value
  if (e?.shiftKey && lastIdx >= 0) { // a range, like in a file manager
    const [a, b] = [Math.min(lastIdx, idx), Math.max(lastIdx, idx)]
    const on = !s.has(h.name)
    for (const r of rows.slice(a, b + 1)) on ? s.add(r.name) : s.delete(r.name)
  } else if (s.has(h.name)) s.delete(h.name)
  else s.add(h.name)
  lastIdx = idx
  picked.value = s
}
const allOn = computed(() => visible.value.length > 0 && visible.value.every((h) => picked.value.has(h.name)))
function pickAll() { picked.value = allOn.value ? new Set() : new Set(visible.value.map((h) => h.name)) }
watch(tab, () => { picked.value = new Set() })
const bulk = ref(null)
const bulkErrors = ref([])
const busy = ref(false)
const plugins = useQuery(() => (bulk.value ? '/api/plugins' : null))
const perHost = computed(() => (plugins.data.value?.plugins || []).filter((p) => p.per_host).flatMap((p) => p.instances.map((i) => ({ name: i.name, plugin: p.name }))))
const allTags = computed(() => uniq(hosts.value.flatMap((h) => h.tags || [])).sort())
const featureList = useQuery(() => (bulk.value ? '/api/features' : null))
const allFeatures = computed(() => (featureList.data.value || []).map((f) => f.path).sort())
function openBulk() {
  bulkErrors.value = []
  bulk.value = { status: '', runs: 'keep', runs_on: '', add_tags: [], remove_tags: [], add_features: [], remove_features: [], zone: 'keep', module: '', enabled: 'on' }
}
async function saveBulk() {
  const b = bulk.value
  const body = { names: [...picked.value] }
  if (b.status) body.status = b.status
  if (b.runs === 'set') body.runs_on = b.runs_on
  if (b.runs === 'clear') body.runs_on = ''
  if (b.zone === 'follow') body.zone = ''
  else if (b.zone !== 'keep') body.zone = b.zone
  if (b.add_features.length) body.add_features = b.add_features
  if (b.remove_features.length) body.remove_features = b.remove_features
  if (b.add_tags.length) body.add_tags = b.add_tags
  if (b.remove_tags.length) body.remove_tags = b.remove_tags
  if (b.module) body.module = { integration: b.module, enabled: { on: true, off: false, auto: null }[b.enabled] }
  busy.value = true
  bulkErrors.value = []
  try {
    const r = await api.post('/api/hosts/bulk', body)
    toast(r.summary)
    bulk.value = null
    picked.value = new Set()
    await Promise.all([query.refresh(), changed()])
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) bulkErrors.value = e.errors
    toast(e.message, 'error')
  } finally { busy.value = false }
}
async function deleteBulk() {
  const n = picked.value.size
  if (!(await confirm({ title: `Delete ${n} host${n === 1 ? '' : 's'}?`, body: `${[...picked.value].slice(0, 6).join(', ')}${n > 6 ? ` and ${n - 6} more` : ''}. Their interfaces, services and DNS records go with them; History can bring each one back.`,
    action: 'Delete', danger: true }))) return
  try {
    const r = await api.post('/api/hosts/bulk', { names: [...picked.value], delete: true })
    toast(r.summary)
    picked.value = new Set()
    await Promise.all([query.refresh(), changed()])
  } catch (e) { toast(e.message, 'error') }
}

const openAddr = ref({})
const addresses = (h) => h.interfaces.filter((i) => i.ip4 || i.ip6)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Hosts</h1>
      <span class="sub">{{ visible.length }} of {{ inTab.length }}</span>
      <span v-if="loading" class="faint small">loading …</span>
      <span class="grow"></span>
      <input v-model="q" type="search" placeholder="Search, or tag:backup zone:DC_TC on:pve …" title="Plain text searches everything; tag: zone: status: on: net: cat: narrow it" style="max-width: 340px"
             aria-label="Search hosts" @input="syncQuery">
      <MenuButton v-if="searchItems.length" :items="searchItems" :icon="Bookmark" label="Saved" title="Saved searches" />
      <RouterLink v-if="can('host', 'edit')" :to="tab === 'vip' ? '/hosts/new?kind=vip' : tab === 'virtual' ? '/hosts/new?virtual=1' : '/hosts/new'" class="btn primary"><Plus :size="15" /> {{ tab === 'vip' ? 'VIP' : 'Host' }}</RouterLink>
    </div>

    <nav class="tabs">
      <a v-for="(label, t) in TAB_LABEL" :key="t" href="#" :class="{ 'router-link-active': tab === t }"
         :aria-current="tab === t ? 'page' : undefined" @click.prevent="setTab(t)">{{ label }} <span class="faint">{{ counts[t] }}</span></a>
    </nav>
    <div class="filters">
      <template v-for="f in facets" :key="f.key">
        <FilterSelect v-if="f.values.length > 1 || filter[f.key]" :label="f.label" :options="f.values" :model-value="filter[f.key]"
                      @update:model-value="(v) => { filter[f.key] = v; syncQuery() }" />
      </template>
      <FilterSelect :model-value="groupBy || null" label="Group by" any="none" :options="GROUPS" @update:model-value="setGroup" />
      <button v-if="Object.values(filter).some(Boolean)" class="btn small ghost" @click="filter = { zone: null, cat: null, status: null, tag: null }; syncQuery()">Clear filters</button>
    </div>

    <div v-if="picked.size" class="bulkbar" role="region" aria-label="Selected hosts">
      <b>{{ picked.size }} selected</b>
      <button class="btn small primary" @click="openBulk"><Pencil :size="14" /> Change</button>
      <RouterLink class="btn small" :to="{ path: '/labels', query: { hosts: [...picked].join(',') } }"><Tag :size="14" /> Labels</RouterLink>
      <button class="btn small danger" @click="deleteBulk"><Trash2 :size="14" /> Delete</button>
      <span class="grow"></span>
      <span class="small faint hide-m">Shift-click selects a range</span>
      <button class="btn icon" aria-label="Clear the selection" title="Clear the selection" @click="picked = new Set()"><X :size="15" /></button>
    </div>

    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th v-if="editable" class="pick"><input type="checkbox" :checked="allOn" :indeterminate="picked.size > 0 && !allOn"
                  :aria-label="allOn ? 'Select none' : 'Select all shown'" @change="pickAll"></th>
              <th class="sortable" @click="sortBy('name')">Name</th>
              <th class="sortable hide-p" @click="sortBy('function')">Function</th>
              <th class="sortable" @click="sortBy('zone')">Zone</th>
              <th class="hide-s">Addresses</th>
              <th class="sortable hide-p" @click="sortBy('status')">Status</th>
              <th v-for="src in sources" :key="src" class="srccol hide-s" :title="`Status from ${src}`">{{ src }}</th>
              <th class="hide-s">Links</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="r in rows" :key="r.key">
            <tr v-if="r.head" class="grph"><td :colspan="(editable ? 7 : 6) + sources.length">
              <b :class="{ mono: groupBy !== 'status' }">{{ r.label || NOGROUP[groupBy] }}</b> <span class="faint small">{{ r.n }}</span>
              <a v-if="r.label && groupBy === 'tag'" href="#" class="small" style="margin-left: 8px" @click.prevent="addTerm('tag', r.label)">only these</a></td></tr>
            <tr v-else v-rowlink class="click" :class="{ sel: picked.has(r.h.name) }" @mouseenter="hover(r.h)"
                @click="router.push(`/hosts/${r.h.uid || r.h.name}`)">
              <td v-if="editable" class="pick" @click.stop="pick(r.h, r.idx, $event)">
                <input type="checkbox" :checked="picked.has(r.h.name)" :aria-label="`Select ${r.h.name}`" @click.stop="pick(r.h, r.idx, $event)"></td>
              <td>
                <div class="namecell">
                <span class="hi"><HostIcon :host="r.h" :size="18" /></span>
                <div>
                <div class="fun" :class="{ none: !r.h.fun }">{{ r.h.fun || '—' }}</div>
                <div class="small show-p">{{ r.h.function }} <span v-if="r.h.status !== 'active'" class="status" :class="`s-${r.h.status}`">{{ r.h.status }}</span></div>
                <div class="mono muted">
                  {{ r.h.name }}
                  <span v-if="!r.h.name_ok && r.h.status !== 'legacy'" class="c-yellow" title="Name doesn't follow the convention">⚠</span>
                </div>
                </div>
                </div>
              </td>
              <td class="hide-p">
                {{ r.h.function }}
                <div v-if="r.h.kind === 'vip'" class="small faint"><span class="vipb">VIP</span> {{ r.h.vip_protocol || '' }} · {{ (r.h.vip_members || []).join(', ') || 'no members' }}</div>
                <div v-else-if="r.h.runs_on" class="small faint">on {{ r.h.runs_on }}</div>
                <div v-else-if="r.h.hardware" class="small faint">{{ r.h.hardware }}</div>
                <div v-if="r.h.tags?.length" class="tagrow"><TagChip v-for="t in r.h.tags" :key="t" :name="t" class="tagbtn" role="button" tabindex="0" :title="`Only hosts tagged ${t}`"
                  @click.stop="addTerm('tag', t)" @keydown.enter.stop.prevent="addTerm('tag', t)" /></div>
              </td>
              <td><ZoneChip :zone="r.h.zone" /></td>
              <td class="hide-s">
                <div v-if="addresses(r.h).length" class="ifs">
                  <template v-for="(i, n) in addresses(r.h).slice(0, openAddr[r.h.name] ? 99 : 3)" :key="n">
                    <span class="net">{{ i.network || i.label }}</span>
                    <span><Copy :text="i.ip4 || i.ip6" /></span>
                  </template>
                </div>
                <a v-if="addresses(r.h).length > 3 && !openAddr[r.h.name]" href="#" class="small" @click.stop.prevent="openAddr[r.h.name] = true">{{ addresses(r.h).length - 3 }} more</a>
                <span v-if="!addresses(r.h).length" class="faint">—</span>
              </td>
              <td class="nowrap hide-p"><span class="ldot" :class="r.h.live" :title="`${LIVE[r.h.live] || r.h.live}${Object.keys(r.h.live_sources || {}).length ? '' : ' (from Discovery)'}`"></span>
                <span class="status" :class="`s-${r.h.status}`">{{ r.h.status }}</span></td>
              <td v-for="src in sources" :key="src" class="srccol hide-s"><span v-if="r.h.live_sources?.[src]" class="ldot" :class="r.h.live_sources[src]" :title="`${src}: ${r.h.live_sources[src]}`"></span></td>
              <td class="nowrap hide-s">
                <a v-for="l in r.h.links" :key="l.url" :href="l.url" target="_blank" rel="noopener" class="small"
                   style="margin-right: 8px" @click.stop>{{ l.name }}</a>
              </td>
            </tr>
            </template>
            <tr v-if="!loading && !visible.length">
              <td :colspan="(editable ? 7 : 6) + sources.length" class="empty">
                <template v-if="hosts.length">Nothing here matches<template v-if="elsewhere.length">, but
                  <template v-for="(t, n) in elsewhere" :key="t">{{ n ? ' and ' : '' }}<a href="#" @click.prevent="setTab(t)">{{ counts[t] }} in {{ TAB_LABEL[t] }}</a></template> do</template>.</template>
                <template v-else>No hosts yet. <RouterLink to="/hosts/new">Add one</RouterLink> or import an inventory under Settings.</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="visible.length > shown" class="more">
        <button class="btn" @click="shown += PAGE">Show {{ Math.min(PAGE, visible.length - shown) }} more</button>
        <span class="faint small">{{ shown }} of {{ visible.length }} shown</span>
      </div>
    </div>

    <Modal v-if="saveDlg" title="Save this search" :busy="busy" @close="saveDlg = null">
      <p class="small muted" style="margin: 0">Saves the search text, the filters, the tab and the grouping as they are now.</p>
      <Field label="Name" :error="saveDlg.error"><input v-model.trim="saveDlg.name" maxlength="64" placeholder="k3s nodes that are up" @keydown.enter="saveDlg.name && saveSearch()"></Field>
      <label v-if="saved.data.value?.can_share" class="check small"><input v-model="saveDlg.shared" type="checkbox"> Share with everyone</label>
      <template #foot>
        <button class="btn ghost" @click="saveDlg = null">Cancel</button>
        <button class="btn primary" :disabled="busy || !saveDlg.name" @click="saveSearch">Save</button>
      </template>
    </Modal>

    <Modal v-if="bulk" :title="`Change ${picked.size} host${picked.size === 1 ? '' : 's'}`" :busy="busy" @close="bulk = null">
      <p class="small muted" style="margin: 0">Only what you set here changes; everything else stays as it is on each host. All of them change, or none.</p>
      <div class="g2">
        <Field label="Status"><select v-model="bulk.status"><option value="">keep</option><option v-for="s in state.meta.statuses" :key="s" :value="s">{{ s }}</option></select></Field>
        <Field label="Runs on">
          <select v-model="bulk.runs"><option value="keep">keep</option><option value="set">set to …</option><option value="clear">nothing (physical)</option></select></Field>
      </div>
      <Field v-if="bulk.runs === 'set'" label="Runs on host">
        <div class="row" style="gap: 8px; flex-wrap: nowrap"><span v-if="bulk.runs_on" class="mono">{{ bulk.runs_on }}</span>
          <HostPicker style="flex: 1" placeholder="Hypervisor or host…" @pick="(n) => (bulk.runs_on = n)" /></div></Field>
      <Field label="Add tags"><ChipInput v-model="bulk.add_tags" :suggestions="allTags" :colour="tagColour" placeholder="backup" label="Add a tag" /></Field>
      <Field label="Remove tags"><ChipInput v-model="bulk.remove_tags" :suggestions="allTags" :colour="tagColour" strict placeholder="old-tag" label="Remove a tag" /></Field>
      <Field label="Add features"><ChipInput v-model="bulk.add_features" :suggestions="allFeatures" strict placeholder="server.packages" label="Add a feature" /></Field>
      <Field label="Remove features"><ChipInput v-model="bulk.remove_features" :suggestions="allFeatures" strict placeholder="server.packages" label="Remove a feature" /></Field>
      <Field label="Zone" hint="Pinning overrides the zone of the network; “follows the network” undoes it">
        <select v-model="bulk.zone"><option value="keep">keep</option><option value="follow">follows its network</option>
          <option v-for="z in state.meta.zones" :key="z.code" :value="z.code">pin to {{ z.code }}</option></select></Field>
      <div class="g2">
        <Field label="Integration" hint="Turn it on or off for these hosts"><select v-model="bulk.module"><option value="">leave as is</option>
          <option v-for="i in perHost" :key="i.name" :value="i.name">{{ i.name }} ({{ i.plugin }})</option></select></Field>
        <Field v-if="bulk.module" label="For these hosts"><select v-model="bulk.enabled"><option value="on">on</option><option value="off">off</option>
          <option value="auto">as its “applies to” says</option></select></Field>
      </div>
      <div v-if="bulkErrors.length" class="errbox small"><div v-for="e in bulkErrors" :key="e.field"><b class="mono">{{ e.field }}</b>: {{ e.message }}</div></div>
      <template #foot>
        <button class="btn ghost" @click="bulk = null">Cancel</button>
        <button class="btn primary" :disabled="busy || (bulk.runs === 'set' && !bulk.runs_on)" @click="saveBulk">Change {{ picked.size }} host{{ picked.size === 1 ? '' : 's' }}</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.grph td { background: var(--panel); padding-top: 10px; padding-bottom: 6px; border-top: 1px solid var(--line-strong); }
.tagbtn { cursor: pointer; }
.tagbtn:hover { border-color: var(--accent); }
.pick { width: 34px; padding-right: 0 !important; cursor: default; }
.pick input { cursor: pointer; }
tr.sel td { background: color-mix(in srgb, var(--accent) 9%, transparent); }
.bulkbar { position: sticky; top: 8px; z-index: 5; display: flex; align-items: center; gap: 10px; padding: 8px 12px; margin-bottom: 10px;
  background: var(--card); border: 1px solid var(--accent); border-radius: var(--r-box); box-shadow: 0 8px 24px var(--shadow); }
@media (max-width: 640px) { .g2 { grid-template-columns: minmax(0, 1fr); } .hide-s { display: none; } }
.namecell { display: flex; gap: 10px; align-items: flex-start; }
.tagrow { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.namecell .hi { color: var(--muted); padding-top: 2px; }
.ldot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--faint); opacity: .45; margin-right: 6px; vertical-align: middle; }
.ldot.up { background: var(--ok); opacity: 1; }
.ldot.down { background: var(--err); opacity: 1; }
.srccol { text-align: center; font-size: calc(11px * var(--text)); white-space: nowrap; }
</style>
