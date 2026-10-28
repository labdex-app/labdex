<script setup>
import { Plus } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '../query'
import Copy from '../components/Copy.vue'
import ZoneChip from '../components/ZoneChip.vue'
import { can, toast } from '../store'
import Loading from '../components/Loading.vue'

const router = useRouter()
const query = useQuery('/api/networks', { onError: (e) => toast(e.message, 'error') })
const nets = computed(() => query.data.value || [])
const loading = computed(() => query.loading.value && !query.data.value)
const q = ref('')



const visible = computed(() => {
  const n = q.value.trim().toLowerCase()
  return n ? nets.value.filter((x) => JSON.stringify(x).toLowerCase().includes(n)) : nets.value
})
const dhcp = (n) => (n.dhcp_start ? `${n.dhcp_start.split('.').pop()}–${n.dhcp_end.split('.').pop()}` : '—')
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Networks</h1>
      <span class="sub">{{ nets.length }}</span>
      <span class="grow"></span>
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
            <tr v-for="n in visible" :key="n.name" class="click" v-rowlink @click="router.push(`/networks/${n.uid || n.name}`)">
              <td><b v-if="n.vlan">{{ n.vlan }}</b><span v-else class="faint">—</span></td>
              <td>
                <b>{{ n.name.toUpperCase() }}</b>
                <div v-if="n.dns_suffix" class="mono faint small">{{ n.dns_suffix }}</div>
                <div v-else class="faint small">not in DNS</div>
              </td>
              <td>
                <div v-for="(p, i) in n.prefixes" :key="i">
                  <Copy v-if="p.prefix" :text="p.prefix" /><span v-else class="mono faint">{{ p.delegated_from }} #{{ p.prefix_id }}/{{ p.length }}</span>
                  <span v-if="p.source === 'delegated'" class="vipb" style="margin-left: 4px">PD</span>
                  <span v-if="p.description" class="small faint" style="margin-left: 4px">{{ p.description }}</span>
                </div>
                <span v-if="!n.prefixes?.length" class="faint">—</span>
              </td>
              <td><ZoneChip :zone="n.zone" /></td>
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
            <tr v-if="loading"><td colspan="9"><Loading /></td></tr>
            <tr v-else-if="!visible.length"><td colspan="9" class="empty">
              <template v-if="(query.data.value || []).length">Nothing matches.</template>
              <template v-else>No networks yet. <RouterLink v-if="can('network', 'edit')" to="/networks/new">Add the first one</RouterLink>: a name, a VLAN and a prefix are enough.</template></td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
