<script setup>
// One IPv4 block of a network as a grid: what every address is. Click a free one to make a
// host with it; click a used one to see who has it.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '../query'
import { can } from '../store'

const props = defineProps({ network: { type: String, required: true } })
const router = useRouter()
const prefix = ref('')
const block = ref(0)
const path = computed(() => `/api/networks/${encodeURIComponent(props.network)}/map?block=${block.value}${prefix.value ? `&prefix=${encodeURIComponent(prefix.value)}` : ''}`)
const q = useQuery(path, { staleMs: 15000 })
const d = computed(() => q.data.value)
const sel = ref(null)
watch(prefix, () => { block.value = 0 })
watch(d, (v) => { if (v && !prefix.value && v.prefix) prefix.value = v.prefix; sel.value = null })

const STATES = {
  host: ['in use', 'A host has it'], gateway: ['gateway', 'The network’s gateway'], dhcp: ['DHCP pool', 'Handed out by DHCP'],
  seen: ['seen, not in labdex', 'Discovery saw something here, but no host has it'], conflict: ['conflict', 'Two hosts have it, or Discovery saw another MAC there'],
  free: ['free', 'Nobody has it'], network: ['network', 'The network address'], broadcast: ['broadcast', 'The broadcast address'],
}
const last = (ip) => ip.split('.').pop()
const cols = computed(() => ((d.value?.cells.length || 0) >= 64 ? 16 : 8))
const tip = (c) => {
  if (c.owners.length) return `${c.ip}: ${c.owners.map((o) => o.host ? `${o.fun || o.host}${o.label ? ` (${o.label})` : ''}` : 'a host you can’t see').join(', ')}`
  if (c.seen) return `${c.ip}: seen${c.seen.hostname ? ` as ${c.seen.hostname}` : ''}${c.seen.mac ? `, ${c.seen.mac}` : ''}`
  return `${c.ip}: ${STATES[c.state][0]}`
}
function open(c) {
  if (c.state === 'free' && can('host', 'edit')) {
    router.push({ path: '/hosts/new', query: { ip: c.ip, network: props.network } })
    return
  }
  sel.value = sel.value?.ip === c.ip ? null : c
}
function adopt(c) {
  router.push({ path: '/hosts/new', query: { ip: c.ip, network: props.network, mac: c.seen?.mac || '', hint: c.seen?.hostname || '', vendor: c.seen?.vendor || '' } })
}
</script>

<template>
  <div class="amap">
    <div v-if="d && !d.prefixes.length" class="card"><p class="faint" style="margin: 0">This network has no IPv4 prefix to draw.</p></div>
    <template v-else-if="d">
      <div class="mapbar">
        <label v-if="d.prefixes.length > 1" class="small">Prefix <select v-model="prefix" style="width: auto">
          <option v-for="p in d.prefixes" :key="p" :value="p">{{ p }}</option></select></label>
        <span v-else class="mono small">{{ d.prefix }}</span>
        <template v-if="d.blocks > 1">
          <button class="btn small" :disabled="block === 0" aria-label="Previous block" @click="block--">‹</button>
          <span class="mono small">{{ d.range[0] }} – {{ d.range[1] }}</span>
          <button class="btn small" :disabled="block >= d.blocks - 1" aria-label="Next block" @click="block++">›</button>
          <span class="faint small">block {{ block + 1 }} of {{ d.blocks }}</span>
        </template>
        <span class="grow"></span>
        <span v-for="(n, s) in d.counts" :key="s" class="lg small" :title="STATES[s]?.[1]"><i :class="`c-${s}`"></i>{{ STATES[s]?.[0] || s }} {{ n }}</span>
      </div>
      <div class="grid" :style="{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }" role="group" :aria-label="`Addresses ${d.range[0]} to ${d.range[1]}`">
        <button v-for="c in d.cells" :key="c.ip" class="cell" :class="[`c-${c.state}`, { pool: c.pool, on: sel?.ip === c.ip, gw: c.gateway }]"
                :title="tip(c)" :aria-label="tip(c)" @click="open(c)">{{ last(c.ip) }}</button>
      </div>
      <div v-if="sel" class="card detail">
        <div class="row"><b class="mono">{{ sel.ip }}</b><span class="lg small"><i :class="`c-${sel.state}`"></i>{{ STATES[sel.state][0] }}</span>
          <span v-if="sel.pool" class="tag">in the DHCP pool</span><span v-if="sel.gateway" class="tag">gateway</span>
          <span class="grow"></span><button class="btn small ghost" @click="sel = null">Close</button></div>
        <div v-for="(o, n) in sel.owners" :key="n" class="small" style="margin-top: 6px">
          <RouterLink v-if="o.host" :to="`/hosts/${o.host}?tab=network`">{{ o.fun || o.host }}</RouterLink><span v-else class="faint">a host you can’t see</span>
          <span v-if="o.label" class="mono faint"> {{ o.label }}</span><span v-if="o.mac" class="mono faint"> · {{ o.mac }}</span>
          <span v-if="o.status" class="faint"> · {{ o.status }}</span>
        </div>
        <div v-if="sel.seen" class="small" style="margin-top: 6px">Discovery saw <span v-if="sel.seen.hostname" class="mono">{{ sel.seen.hostname }}</span>
          <span v-if="sel.seen.mac" class="mono"> {{ sel.seen.mac }}</span><span v-if="sel.seen.vendor" class="faint"> ({{ sel.seen.vendor }})</span>
          <span v-if="sel.state === 'conflict' && sel.owners[0]?.mac && sel.seen.mac !== sel.owners[0].mac" class="c-red"> — not the MAC the inventory has</span></div>
        <button v-if="sel.state === 'seen' && can('host', 'edit')" class="btn small primary" style="margin-top: 10px" @click="adopt(sel)">Add it as a host</button>
      </div>
      <p class="hint">Click a free address to make a host with it, a used one to see who has it. Shaded cells are in the DHCP pool.</p>
    </template>
  </div>
</template>

<style scoped>
.mapbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.grid { display: grid; gap: 3px; }
.cell { font: inherit; font-family: var(--mono); font-size: calc(11px * var(--text)); height: 28px; border-radius: 4px; border: 1px solid transparent; cursor: pointer;
  background: var(--panel); color: var(--faint); padding: 0; position: relative; }
.cell:hover, .cell.on { border-color: var(--ink); }
.cell:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.cell.pool { background-image: repeating-linear-gradient(135deg, transparent 0 5px, color-mix(in srgb, var(--ink) 7%, transparent) 5px 7px); }
.c-host { background-color: color-mix(in srgb, var(--accent) 38%, var(--panel)); color: var(--ink); }
.c-gateway { background-color: color-mix(in srgb, var(--c-blue, #60a5fa) 40%, var(--panel)); color: var(--ink); }
.cell.gw::after { content: ''; position: absolute; top: 2px; right: 2px; width: 5px; height: 5px; border-radius: 50%; background: var(--c-blue, #60a5fa); }
.c-dhcp { background-color: color-mix(in srgb, var(--ok) 14%, var(--panel)); }
.c-seen { background-color: color-mix(in srgb, var(--warn) 34%, var(--panel)); color: var(--ink); }
.c-conflict { background-color: color-mix(in srgb, var(--err) 55%, var(--panel)); color: #fff; }
.c-network, .c-broadcast { background-color: var(--bg); color: var(--faint); }
.lg { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); }
.lg i { width: 11px; height: 11px; border-radius: 3px; display: inline-block; border: 1px solid var(--line); }
.lg i.c-free { background: var(--panel); }
.detail { margin-top: 12px; }
@media (max-width: 640px) { .grid { grid-template-columns: repeat(8, minmax(0, 1fr)) !important; } }
</style>
