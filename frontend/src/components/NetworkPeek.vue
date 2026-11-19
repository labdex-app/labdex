<script setup>
// The brief view of a network beside the Networks list: prefixes, gateway, DHCP, how full it is.
import { computed } from 'vue'
import { Grid3x3, Server } from 'lucide-vue-next'
import Drawer from './Drawer.vue'
import ZoneChip from './ZoneChip.vue'
import Copy from './Copy.vue'
import { state } from '../store'

const props = defineProps({ net: { type: Object, required: true } })
defineEmits(['close'])
const n = computed(() => props.net)
const to = computed(() => `/networks/${n.value.uid || n.value.name}`)
const multiSite = computed(() => (state.meta?.sites || []).length > 1)
</script>

<template>
  <Drawer :label="n.vlan ? `Network · VLAN ${n.vlan}` : 'Network'" :title="n.name" @close="$emit('close')">
    <div class="facts">
      <span v-if="multiSite" class="h-tag">site {{ n.site }}</span>
      <ZoneChip :zone="n.zone" />
      <span class="h-tag">{{ n.dns_suffix || 'not in DNS' }}</span>
    </div>

    <template v-if="n.stats?.size && n.stats.size < 70000">
      <h3 class="label sect">Static addresses</h3>
      <div class="bar" :class="{ hot: n.stats.percent >= 80 }"><div :style="{ width: Math.min(n.stats.percent, 100) + '%' }"></div></div>
      <div class="small muted mt-1 mono">{{ n.stats.used }} used · {{ n.stats.free }} free · {{ n.stats.percent }}%</div>
    </template>

    <h3 class="label sect">Prefixes</h3>
    <dl class="kv">
      <template v-for="(p, i) in n.prefixes || []" :key="i">
        <dt>IPv{{ p.family || '?' }}</dt>
        <dd><Copy v-if="p.prefix" :text="p.prefix" /><span v-else class="mono faint">{{ p.delegated_from }} #{{ p.prefix_id }}/{{ p.length }}</span>
          <span v-if="p.gateway" class="small faint"> gateway <span class="mono">{{ p.gateway }}</span></span>
          <span v-if="p.description" class="small faint"> · {{ p.description }}</span></dd>
      </template>
      <dt>DHCP</dt><dd class="mono">{{ n.dhcp_start ? `${n.dhcp_start} – ${n.dhcp_end}` : '—' }}</dd>
      <dt>Hosts</dt><dd>{{ n.hosts || 0 }}</dd>
    </dl>
    <template v-if="n.notes">
      <h3 class="label sect">Notes</h3>
      <p class="m-0 muted">{{ n.notes }}</p>
    </template>

    <template #foot>
      <RouterLink :to="{ path: '/hosts', query: { q: `net:${n.name}` } }" class="btn"><Server :size="15" /> Its hosts</RouterLink>
      <RouterLink :to="`${to}?tab=map`" class="btn"><Grid3x3 :size="15" /> Address map</RouterLink>
      <span class="grow"></span>
      <RouterLink :to="to" class="btn primary">Open network</RouterLink>
    </template>
  </Drawer>
</template>

<style scoped>
.facts { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.sect { margin: 20px 0 8px; }
</style>
