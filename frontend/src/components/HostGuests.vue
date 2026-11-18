<script setup>
// The guests that run on a host (VMs, containers), with their state, address, use and updates.
// From the hosts list and the patch states, so it shows what the rest of labdex shows.
import { computed } from 'vue'
import { useQuery } from '../query'
import Usage from './Usage.vue'
import ZoneChip from './ZoneChip.vue'

const props = defineProps({ host: { type: String, required: true }, limit: { type: Number, default: 0 } })
const { data: hosts } = useQuery('/api/hosts')
const { data: pt } = useQuery('/api/patches')
const LIVE = { up: 'ok', down: 'err', unknown: 'unknown' }
const all = computed(() => (hosts.value || []).filter((h) => h.runs_on === props.host))
const rows = computed(() => (props.limit ? all.value.slice(0, props.limit) : all.value))
const updates = computed(() => Object.fromEntries((pt.value?.hosts || []).map((p) => [p.name, p])))
const addr = (h) => (h.interfaces || []).map((i) => i.ip4 || i.ip6).find(Boolean) || ''
const gb = (b) => (b ? `${(b / 2 ** 30).toFixed(b < 10 * 2 ** 30 ? 1 : 0)} GB` : '')
defineExpose({ count: computed(() => all.value.length) })
</script>

<template>
  <div v-if="!hosts" class="faint small pad">Loading…</div>
  <p v-else-if="!all.length" class="empty-line pad">Nothing runs on this host.</p>
  <div v-else class="table-wrap">
    <table>
      <thead><tr><th>Name</th><th>Function</th><th>Zone</th><th>Address</th><th>CPU</th><th>Memory</th><th>Updates</th></tr></thead>
      <tbody>
        <tr v-for="g in rows" :key="g.name">
          <td><span class="dot" :class="LIVE[g.live] || 'unknown'" aria-hidden="true"></span>
            <RouterLink :to="`/hosts/${g.uid || g.name}`" class="mono">{{ g.name }}</RouterLink></td>
          <td class="small clip">{{ g.function || '—' }}</td>
          <td><ZoneChip v-if="g.zone" :zone="g.zone" /></td>
          <td class="mono small">{{ addr(g) || '—' }}</td>
          <td><Usage :value="g.usage?.cpu ?? null" label="CPU" :bar="false" /></td>
          <td><Usage :value="g.usage?.mem ?? null" label="memory" :bar="false" /><span v-if="g.usage?.mem_total" class="faint small"> of {{ gb(g.usage.mem_total) }}</span></td>
          <td class="mono" :class="{ 'c-warn': updates[g.name]?.pending }">{{ updates[g.name] ? updates[g.name].pending : '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.pad { padding: 12px 18px; margin: 0; }
td .dot { margin-right: 8px; }
.c-warn { color: var(--warn); }
.clip { max-width: 22ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
