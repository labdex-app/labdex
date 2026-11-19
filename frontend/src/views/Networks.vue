<script setup>
import { Plus } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery } from '../query'
import Copy from '../components/Copy.vue'
import ZoneChip from '../components/ZoneChip.vue'
import NetworkPeek from '../components/NetworkPeek.vue'
import { can, state, toast } from '../store'
import Loading from '../components/Loading.vue'

const router = useRouter()
const query = useQuery('/api/networks', { onError: (e) => toast(e.message, 'error') })
const nets = computed(() => query.data.value || [])
const loading = computed(() => query.loading.value && !query.data.value)
const q = ref('')
const route = useRoute()
const site = ref(route.query.site || '')
const sites = computed(() => state.meta.sites || [])

const visible = computed(() => {
  const n = q.value.trim().toLowerCase()
  const inSite = site.value ? nets.value.filter((x) => x.site === site.value) : nets.value
  return n ? inSite.filter((x) => JSON.stringify(x).toLowerCase().includes(n)) : inSite
})
// list-detail: a click shows the network beside the list (?peek=<uid>); a double click opens it
const peeked = computed(() => route.query.peek ? nets.value.find((n) => n.uid === route.query.peek || n.name === route.query.peek) : null)
function peek(n) { router.replace({ query: { ...route.query, peek: n.uid || n.name } }) }
function unpeek() { const { peek: _, ...rest } = route.query; router.replace({ query: rest }) }
const dhcp = (n) => (n.dhcp_start ? `${n.dhcp_start.split('.').pop()}–${n.dhcp_end.split('.').pop()}` : '—')
</script>

<template>
  <div class="page" :class="{ peeking: peeked }">
    <div class="page-head">
      <h1>Networks</h1>
      <span class="sub">{{ nets.length }}</span>
      <span class="grow"></span>
      <select v-if="sites.length > 1" v-model="site" aria-label="Site" style="max-width: 180px">
        <option value="">Every site</option>
        <option v-for="s in sites" :key="s.code" :value="s.code">{{ s.name }}</option>
      </select>
      <input v-model="q" type="search" placeholder="Search" style="max-width: 260px" aria-label="Search networks">
      <RouterLink v-if="can('network', 'edit')" to="/networks/new" class="btn primary"><Plus :size="15" /> Network</RouterLink>
    </div>

    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>VLAN</th><th>Name</th><th>Prefix</th><th>Zone</th><th>Gateway</th><th>DHCP</th><th>Static usage</th><th>Hosts</th><th>Notes</th></tr>
          </thead>
          <tbody>
            <tr v-for="n in visible" :key="n.name" class="click" :class="{ peek: peeked && peeked.name === n.name }" v-rowlink
                :aria-expanded="peeked && peeked.name === n.name ? 'true' : 'false'" @click="peek(n)" @dblclick="router.push(`/networks/${n.uid || n.name}`)">
              <td><b v-if="n.vlan">{{ n.vlan }}</b><span v-else class="faint">—</span></td>
              <td>
                <b>{{ n.name.toUpperCase() }}</b>
                <div v-if="n.dns_suffix" class="mono faint small">{{ n.dns_suffix }}</div>
                <div v-else class="faint small">not in DNS</div>
              </td>
              <td>
                <div v-for="(p, i) in n.prefixes" :key="i">
                  <Copy v-if="p.prefix" :text="p.prefix" /><span v-else class="mono faint">{{ p.delegated_from }} #{{ p.prefix_id }}/{{ p.length }}</span>
                  <span v-if="p.source === 'delegated'" class="vipb ml-1">PD</span>
                  <span v-if="p.description" class="small faint ml-1">{{ p.description }}</span>
                </div>
                <span v-if="!n.prefixes?.length" class="faint">—</span>
              </td>
              <td><ZoneChip :zone="n.zone" /><span v-if="sites.length > 1" class="small faint ml-1">{{ n.site }}</span></td>
              <td class="mono">{{ n.gateway4 || '—' }}</td>
              <td class="mono nowrap">{{ dhcp(n) }}</td>
              <td style="min-width: 130px">
                <template v-if="n.stats.size && n.stats.size < 70000">
                  <div class="bar" :class="{ hot: n.stats.percent >= 80 }"><div :style="{ width: Math.min(n.stats.percent, 100) + '%' }"></div></div>
                  <div class="small faint">{{ n.stats.used }} used · {{ n.stats.free }} free</div>
                </template>
                <span v-else class="small faint">{{ n.stats.used }} used</span>
              </td>
              <td>{{ n.hosts || '' }}</td>
              <td class="muted">{{ n.notes }}</td>
            </tr>
            <tr v-if="!loading && !visible.length"><td colspan="9" class="empty">
              <template v-if="(query.data.value || []).length">Nothing matches.</template>
              <template v-else>No networks yet. <RouterLink v-if="can('network', 'edit')" to="/networks/new">Add the first one</RouterLink>: a name, a VLAN and a prefix are enough.</template></td></tr>
          </tbody>
        </table>
        <Loading v-if="loading" :rows="5" label="Loading networks" />
      </div>
    </div>
    <NetworkPeek v-if="peeked" :key="peeked.name" :net="peeked" @close="unpeek" />
  </div>
</template>

<style scoped>
tr.peek td { background: var(--accent-faint); }
tr.peek td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
@media (min-width: 1200px) { .page.peeking { padding-right: 524px; } }
</style>
