<script setup>
import AddressMap from '../components/AddressMap.vue'
import { useDraft, useUnsaved } from '../unsaved'
import { confirm } from '../confirm'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import Copy from '../components/Copy.vue'
import Field from '../components/Field.vue'
import PrefixEditor from '../components/PrefixEditor.vue'
import ZoneChip from '../components/ZoneChip.vue'
import { changed, state, toast, stamp } from '../store'

const props = defineProps({ name: { type: String, required: true } })
const router = useRouter()
const isNew = computed(() => props.name === 'new')

const blank = () => ({ name: '', vlan: '', zone: state.meta.zones[0]?.code || '', prefixes: [], in_dns: true, dns_zone: '', notes: '' })
const form = reactive(blank())
const saved = ref('')
const net = ref(null)
const errors = ref({})
const busy = ref(false)
const notFound = ref(false)

const payload = () => {
  const { prefix4, prefix6, gateway4, dhcp_start, dhcp_end, ...rest } = form // eslint-disable-line no-unused-vars
  return { ...rest, vlan: form.vlan === '' || form.vlan == null ? null : Number(form.vlan),
           prefixes: form.prefixes.map((p) => ({ ...p })) }
}
const dirty = computed(() => !!saved.value && JSON.stringify(payload()) !== saved.value) // nothing to lose before it loaded
const unsaved = useUnsaved(dirty, () => save())
const draft = useDraft(() => `network:${isNew.value ? 'new' : props.name}`, () => form, (d) => Object.assign(form, d), dirty, computed(() => !!saved.value))

function fill(n) {
  Object.assign(form, blank(), { ...n, vlan: n.vlan ?? '', prefixes: (n.prefixes || []).map((p) => ({
    delegated_from: 'wan', length: 64, ...p, prefix_id: p.prefix_id == null ? '' : `0x${Number(p.prefix_id).toString(16)}` })) })
  saved.value = JSON.stringify(payload())
}

async function load() {
  if (isNew.value) { fill(blank()); return }
  try {
    const n = await api.get(`/api/networks/${encodeURIComponent(props.name)}?free=12`)
    if (n.uid && props.name !== n.uid) { router.replace({ path: `/networks/${n.uid}`, query: route.query }); return } // the stable id survives renames
    net.value = n
    document.title = `${n.name} · Network · labdex`
    fill(n)
  } catch (e) {
    if (e.status === 404) notFound.value = true
    else toast(e.message, 'error')
  }
}
onMounted(load)

// For a new network, suggest prefix and gateway from the VLAN, following
// "third octet = VLAN" and the .254 gateway convention of the existing networks.
const base = ref('')
onMounted(async () => {
  if (!isNew.value) return
  const nets = await api.get('/api/networks').catch(() => [])
  const counts = {}
  for (const n of nets) {
    const m = /^(\d+\.\d+)\.(\d+)\.0\/24$/.exec(n.prefix4 || '')
    if (m && Number(m[2]) === n.vlan) counts[m[1]] = (counts[m[1]] || 0) + 1
  }
  base.value = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] || ''
})
const suggestion = computed(() => {
  const v = Number(form.vlan)
  if (!isNew.value || !base.value || !v || v > 255 || form.prefixes.some((p) => p.family === 4 && p.prefix)) return null
  return { prefix4: `${base.value}.${v}.0/24`, gateway4: `${base.value}.${v}.254` }
})

async function save() {
  busy.value = true
  errors.value = {}
  try {
    const n = isNew.value
      ? await api.post('/api/networks', payload())
      : await api.put(`/api/networks/${encodeURIComponent(props.name)}`, payload())
    draft.clear()
    toast(`Saved ${n.name}`)
    await changed()
    if (isNew.value) { unsaved.release(); router.replace(`/networks/${n.uid}`) }
    else await load()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) {
      errors.value = e.fields
      toast('Please fix the highlighted fields', 'error')
    } else toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}

async function remove() {
  const name = net.value?.name || props.name
  if (!(await confirm({ title: `Delete network ${name}?`, action: 'Delete', danger: true }))) return
  try {
    await api.del(`/api/networks/${encodeURIComponent(props.name)}`)
    toast(`Deleted ${name}`)
    await changed()
    unsaved.release()
    router.push('/networks')
  } catch (e) {
    toast(e.message, 'error')
  }
}

const err = (f) => errors.value[f]
function useSuggestion() {
  const s = suggestion.value
  const p4 = form.prefixes.find((p) => p.family === 4)
  if (p4) Object.assign(p4, { prefix: s.prefix4, gateway: s.gateway4 })
  else form.prefixes.unshift({ family: 4, prefix: s.prefix4, source: 'static', gateway: s.gateway4, dhcp_start: '', dhcp_end: '', description: '' })
}
const route = useRoute()
const tab = ref(String(route.query.tab || 'overview'))
function setTab(t) { tab.value = t; router.replace({ query: t === 'overview' ? {} : { tab: t } }) }
const tabs = computed(() => [
  { id: 'overview', label: 'Overview', err: Object.keys(errors.value).length > 0 },
  { id: 'map', label: 'Address map', hide: !net.value?.prefix4 },
  { id: 'usage', label: 'Usage', count: net.value?.used.length },
  { id: 'hosts', label: 'Hosts', count: net.value?.members.length },
])
</script>

<template>
  <div class="page">
    <div v-if="notFound" class="errbox">No network called “{{ name }}”. <RouterLink to="/networks">Back</RouterLink></div>
    <template v-else>
      <div v-if="draft.offer.value" class="draftbar" role="status">
        <span>Changes you didn’t save from {{ stamp(new Date(draft.offer.value.at).toISOString()) }} are kept in this browser.</span>
        <span class="grow"></span>
        <button class="btn small primary" @click="draft.restore()">Restore them</button>
        <button class="btn small ghost" @click="draft.discard()">Discard</button>
      </div>
      <div class="page-head">
        <RouterLink to="/networks" class="btn ghost small">← Networks</RouterLink>
        <h1>{{ isNew ? 'New network' : (net?.name || '').toUpperCase() }}</h1>
        <span v-if="!isNew && net?.vlan" class="sub">VLAN {{ net.vlan }}</span>
        <ZoneChip v-if="form.zone" :zone="form.zone" />
        <span class="grow"></span>
        <span v-if="!isNew && net?.can_edit === false" class="lock">🔒 read-only for you</span>
        <template v-else>
          <button v-if="!isNew" class="btn danger" @click="remove">Delete</button>
          <button class="btn primary" :disabled="busy || (!dirty && !isNew)" @click="save">
            {{ busy ? 'Saving…' : isNew ? 'Create network' : dirty ? 'Save changes' : 'Saved' }}
          </button>
        </template>
      </div>

      <nav v-if="!isNew" class="tabs" aria-label="Network sections">
        <a v-for="t in tabs.filter((x) => !x.hide)" :key="t.id" href="#" :class="{ 'router-link-active': tab === t.id }" :aria-current="tab === t.id ? 'page' : undefined" @click.prevent="setTab(t.id)">
          {{ t.label }}<span v-if="t.count" class="tcount">{{ t.count }}</span><span v-if="t.err" class="pill ml-2">!</span>
        </a>
      </nav>

      <div v-show="isNew || tab === 'overview'" class="split">
        <form class="card" @submit.prevent="save">
          <div class="grid2">
            <Field label="Name" :error="err('name')" hint="Lowercase; also the DNS subdomain unless the network has a DNS zone of its own">
              <input v-model.trim="form.name" type="text" class="mono" placeholder="main" :class="{ invalid: err('name') }">
            </Field>
            <Field label="VLAN" :error="err('vlan')" hint="Empty for tunnels and external ranges">
              <input v-model="form.vlan" type="number" min="1" max="4094" :class="{ invalid: err('vlan') }">
            </Field>
            <Field label="Zone" :error="err('zone')">
              <select v-model="form.zone">
                <optgroup v-for="a in state.meta.areas" :key="a.code" :label="`${a.code} · ${a.name}`">
                  <option v-for="z in state.meta.zones.filter((x) => x.area === a.code)" :key="z.code" :value="z.code">{{ z.code }} · {{ z.long }}</option>
                </optgroup>
              </select>
            </Field>
          </div>
          <div class="mt-4">
            <PrefixEditor v-model:prefixes="form.prefixes" :errors="errors" :vlan="form.vlan" />
          </div>
          <div class="stack mt-3">
            <label class="check"><input v-model="form.in_dns" type="checkbox"> Publish DNS records for this network</label>
            <Field v-if="form.in_dns" label="DNS zone" :error="err('dns_zone')"
                   :hint="`Where its address records go. Empty: <host>.${form.name || 'network'}.${state.meta.settings.domain}; @: <host>.${state.meta.settings.domain}; or a zone of its own, e.g. home.lab → <host>.home.lab. Short names and fun names stay in the host's zone.`">
              <input v-model.trim="form.dns_zone" type="text" class="mono" :placeholder="`${form.name || 'network'}.${state.meta.settings.domain}`" spellcheck="false">
            </Field>
            <Field label="Notes"><textarea v-model="form.notes" rows="2" style="font-family: inherit; font-size: calc(14px * var(--text))"></textarea></Field>
          </div>
          <button type="submit" hidden></button>
          <div v-if="suggestion" class="row mt-3">
            <span class="hint m-0">Following your convention: <code>{{ suggestion.prefix4 }}</code>, gateway <code>{{ suggestion.gateway4 }}</code></span>
            <button type="button" class="btn small" @click="useSuggestion">Use it</button>
          </div>
        </form>

        <aside v-if="net">
          <div v-if="net.aliases.length" class="card">
            <h3>Firewall aliases</h3>
            <ul class="plain"><li v-for="a in net.aliases" :key="a.name"><Copy :text="a.name" /> <span class="faint small">{{ a.content.join(', ') }}</span></li></ul>
          </div>
        </aside>
      </div>

      <div v-if="net && tab === 'usage'" class="split">
        <div class="card flush">
          <div class="table-wrap">
            <table>
              <thead><tr><th>Address</th><th>Host</th><th>Label</th></tr></thead>
              <tbody>
                <tr v-for="u in net.used" :key="u.ip">
                  <td><Copy :text="u.ip" /></td>
                  <td>
                    <RouterLink v-if="u.host" :to="`/hosts/${u.host}`">{{ u.fun || u.host }}</RouterLink>
                    <span v-else class="faint">—</span>
                  </td>
                  <td class="muted">{{ u.label }}</td>
                </tr>
                <tr v-if="!net.used.length"><td colspan="3" class="empty">No addresses in use.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <aside>
          <div v-for="(u, i) in net.usage" :key="i" class="card">
            <h3>{{ u.prefix || 'delegated (not seen yet)' }} <span class="faint small">IPv{{ u.family }}<template v-if="u.description"> · {{ u.description }}</template></span></h3>
            <template v-if="u.family === 4 && u.size && u.size < 70000">
              <div class="bar" :class="{ hot: u.percent >= 80 }"><div :style="{ width: Math.min(u.percent, 100) + '%' }"></div></div>
              <p class="small muted">{{ u.used }} used · {{ u.size - u.dhcp_size - u.used }} static free · {{ u.dhcp_size }} in the DHCP pool</p>
            </template>
            <p v-else class="small muted">{{ u.used }} address(es) in use<template v-if="u.gateway"> · gateway <span class="mono">{{ u.gateway }}</span></template></p>
            <template v-if="u.free_tokens?.length">
              <h3 class="mt-3">Next free interface IDs</h3>
              <div class="row" style="gap: 6px 12px"><Copy v-for="t in u.free_tokens" :key="t" :text="t" /></div>
            </template>
            <template v-if="u.free.length">
              <h3 class="mt-3">Next free (static)</h3>
              <div class="row" style="gap: 6px 12px"><Copy v-for="ip in u.free" :key="ip" :text="ip" /></div>
            </template>
          </div>
          <div v-if="!net.usage.length" class="card"><p class="empty-line">No prefixes on this network.</p></div>
        </aside>
      </div>

      <AddressMap v-if="net && tab === 'map'" :network="net.name" />
      <div v-if="net && tab === 'hosts'" class="card flush">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Host</th><th>Function</th><th>Zone</th><th>Addresses here</th><th>MAC</th><th>Status</th></tr></thead>
            <tbody>
              <tr v-for="m in net.members" :key="m.name" class="click" v-rowlink @click="router.push(`/hosts/${m.name}?tab=network`)">
                <td>
                  <div class="fun" :class="{ none: !m.fun }">{{ m.fun || '—' }}</div>
                  <div class="mono muted">{{ m.name }} <span v-if="m.kind === 'vip'" class="vipb">VIP</span></div>
                </td>
                <td>{{ m.function }}</td>
                <td><ZoneChip :zone="m.zone" /><span v-if="m.zone !== form.zone" class="small faint" title="Its home zone differs from this network's"> home</span></td>
                <td class="mono small">
                  <div v-for="(a, i) in m.addresses" :key="i">{{ [a.ip4, a.ip6].filter(Boolean).join('  ') || '—' }}
                    <span v-if="a.label" class="faint">{{ a.label }}</span><span v-if="a.primary" class="faint"> · primary</span></div>
                </td>
                <td class="mono small"><div v-for="(a, i) in m.addresses" :key="i">{{ a.mac || '—' }}</div></td>
                <td><span class="status" :class="`s-${m.status}`">{{ m.status }}</span></td>
              </tr>
              <tr v-if="!net.members.length"><td colspan="6" class="empty">No host has an interface in this network.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>
