<script setup>
// The brief view of a host beside the Hosts list: what it is, where it sits, its addresses,
// services and links. Edit turns it into the quick edit form (HostQuickEdit); the rest is on
// the host's own page.
import { computed } from 'vue'
import { ArrowUpRight, Pencil, Unplug } from 'lucide-vue-next'
import Drawer from './Drawer.vue'
import ZoneChip from './ZoneChip.vue'
import TagChip from './TagChip.vue'
import { state } from '../store'

const props = defineProps({ host: { type: Object, required: true } })
defineEmits(['close', 'edit'])
const h = computed(() => props.host)
const to = computed(() => `/hosts/${h.value.uid || h.value.name}`)
const LIVE = { up: 'ok', down: 'err', degraded: 'warn' }
const ifs = computed(() => (h.value.interfaces || []).filter((i) => i.ip4 || i.ip6 || i.network))
const multiSite = computed(() => (state.meta?.sites || []).length > 1)
</script>

<template>
  <Drawer :label="`Host · ${h.kind || 'device'}`" :title="h.name" @close="$emit('close')">
    <div class="who">
      <span class="dot" :class="LIVE[h.live] || 'unknown'" aria-hidden="true"></span>
      <span class="fun" :class="{ none: !h.fun }">{{ h.fun || 'no fun name' }}</span>
      <span class="status" :class="`s-${h.status}`">{{ h.status }}</span>
      <span v-if="h.live && h.live !== 'unknown'" class="small muted">{{ h.live }}</span>
    </div>
    <p v-if="h.function" class="m-0 mb-3">{{ h.function }}</p>
    <div class="facts">
      <span v-if="multiSite" class="h-tag">site {{ h.site }}</span>
      <ZoneChip :zone="h.zone" />
      <span v-if="h.runs_on" class="h-tag">on {{ h.runs_on }}</span>
      <span v-if="h.rack" class="h-tag">rack {{ h.rack }}<template v-if="h.rack_unit"> U{{ h.rack_unit }}</template></span>
      <TagChip v-for="t in h.tags || []" :key="t" :name="t" />
    </div>

    <h3 class="label sect">Interfaces</h3>
    <table v-if="ifs.length" class="mini">
      <thead><tr><th>Port</th><th>Network</th><th>Address</th></tr></thead>
      <tbody>
        <tr v-for="(i, n) in ifs" :key="n">
          <td class="mono">{{ i.label || '—' }}</td>
          <td><RouterLink v-if="i.network" :to="`/networks/${i.network}`">{{ i.network }}</RouterLink></td>
          <td class="mono">{{ i.ip4 }}<div v-if="i.ip6" class="faint small">{{ i.ip6 }}</div></td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-line">No addresses.</p>

    <template v-if="(h.services || []).length">
      <h3 class="label sect">Services</h3>
      <ul class="plain">
        <li v-for="s in h.services" :key="s.name"><span class="mono">{{ s.name }}</span>{{ " " }}
          <span v-if="s.port" class="faint small">{{ s.proto || 'tcp' }}/{{ s.port }}</span></li>
      </ul>
    </template>
    <template v-if="(h.links || []).length">
      <h3 class="label sect">Open</h3>
      <div class="row"><a v-for="l in h.links" :key="l.url" :href="l.url" target="_blank" rel="noopener" class="btn small">{{ l.name }} <ArrowUpRight :size="13" /></a></div>
    </template>
    <template v-if="h.notes">
      <h3 class="label sect">Notes</h3>
      <p class="m-0 notes">{{ h.notes }}</p>
    </template>

    <template #foot>
      <RouterLink :to="`/impact?host=${encodeURIComponent(h.name)}`" class="btn"><Unplug :size="15" /> What breaks?</RouterLink>
      <span class="grow"></span>
      <button v-if="h.can_edit" class="btn" @click="$emit('edit')"><Pencil :size="14" /> Edit</button>
      <RouterLink :to="to" class="btn primary">Open host</RouterLink>
    </template>
  </Drawer>
</template>

<style scoped>
.who { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.facts { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.sect { margin: 20px 0 8px; }
table.mini th { padding: 6px 10px; }
table.mini td { padding: 7px 10px; }
table.mini { border: 1px solid var(--line); border-radius: var(--r-box); border-collapse: separate; border-spacing: 0; overflow: hidden; }
.notes { white-space: pre-wrap; color: var(--muted); }
</style>
