<script setup>
// Address ranges of a network: IPv4 and IPv6, any number of each.
// Gateway and DHCP range take a full address or a suffix (254, ::1).
import { Plus, X } from 'lucide-vue-next'
import { computed } from 'vue'

// v-model:prefixes: the network's prefixes, edited in place
const prefixes = defineModel('prefixes', { type: Array, required: true })
const props = defineProps({
  errors: { type: Object, default: () => ({}) },
  vlan: { type: [String, Number], default: '' },
})

const MASKS = {
  4: [8, 12, 16, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32],
  6: [32, 40, 44, 48, 52, 56, 60, 62, 63, 64, 72, 80, 96, 112, 120, 124, 126, 127, 128],
}
const err = (i, k) => props.errors[`prefixes[${i}].${k}`]
const anyErr = (i) => Object.keys(props.errors).some((k) => k.startsWith(`prefixes[${i}]`))

function split(p) {
  const [addr, len] = String(p.prefix || '').split('/')
  return { addr: addr || '', len: len ? Number(len) : (p.family === 6 ? 64 : 24) }
}
function setAddr(p, v) {
  const s = split(p)
  // pasting a whole CIDR sets both parts
  const m = /^(.+)\/(\d{1,3})$/.exec(v.trim())
  p.prefix = m ? `${m[1]}/${m[2]}` : (v.trim() ? `${v.trim()}/${s.len}` : '')
}
function setLen(p, v) { const s = split(p); p.prefix = s.addr ? `${s.addr}/${v}` : '' }
function setFamily(p, f) {
  p.family = Number(f)
  if (p.family === 4) { p.source = 'static' }
  p.prefix = ''
  p.gateway = ''; p.dhcp_start = ''; p.dhcp_end = ''
}
function add(family) {
  prefixes.value.push({ family, prefix: '', source: 'static', delegated_from: 'wan', prefix_id: props.vlan ? `0x${Number(props.vlan).toString(16)}` : '',
                        length: 64, gateway: family === 4 ? '254' : '', dhcp_start: '', dhcp_end: '', description: '' })
}
const remove = (i) => prefixes.value.splice(i, 1)
const counts = computed(() => ({ 4: prefixes.value.filter((p) => p.family === 4).length, 6: prefixes.value.filter((p) => p.family === 6).length }))
</script>

<template>
  <div class="pfx">
    <div class="row" style="margin-bottom: 8px">
      <span class="flabel" style="margin: 0">Addresses</span>
      <span class="small faint">{{ counts[4] }} IPv4 · {{ counts[6] }} IPv6</span>
      <span class="grow"></span>
      <button type="button" class="btn small" @click="add(4)"><Plus :size="14" /> IPv4</button>
      <button type="button" class="btn small" @click="add(6)"><Plus :size="14" /> IPv6</button>
    </div>
    <div v-for="(p, i) in prefixes" :key="i" class="pfx-row" :class="{ bad: anyErr(i) }">
      <div class="pfx-top">
        <select :value="p.family" class="fam small auto" aria-label="Family" @change="setFamily(p, $event.target.value)">
          <option :value="4">IPv4</option><option :value="6">IPv6</option>
        </select>
        <select v-if="p.family === 6" v-model="p.source" class="src small auto" aria-label="Source">
          <option value="static">static</option>
          <option value="delegated">delegated</option>
        </select>
        <template v-if="p.source !== 'delegated'">
          <input :value="split(p).addr" class="mono addr" :placeholder="p.family === 4 ? '10.0.10.0' : 'fd00:0:0:10::'"
                 :class="{ invalid: err(i, 'prefix') }" aria-label="Prefix" spellcheck="false" @change="setAddr(p, $event.target.value)">
          <span class="dash">/</span>
          <select :value="split(p).len" class="len small auto" aria-label="Mask" @change="setLen(p, $event.target.value)">
            <option v-for="m in MASKS[p.family]" :key="m" :value="m">{{ m }}{{ p.family === 4 ? ` (${m >= 31 ? Math.pow(2, 32 - m) : Math.pow(2, 32 - m) - 2})` : '' }}</option>
          </select>
        </template>
        <template v-else>
          <span class="small faint">from</span>
          <input v-model.trim="p.delegated_from" class="mono short" placeholder="wan" aria-label="WAN interface" :class="{ invalid: err(i, 'delegated_from') }">
          <span class="small faint">prefix ID</span>
          <input v-model.trim="p.prefix_id" class="mono short" placeholder="0x10" aria-label="Prefix ID" :class="{ invalid: err(i, 'prefix_id') }">
          <span class="dash">/</span>
          <select v-model.number="p.length" class="len small auto" aria-label="Length"><option v-for="m in [48, 52, 56, 60, 62, 63, 64]" :key="m" :value="m">{{ m }}</option></select>
          <span v-if="p.prefix" class="small faint mono" title="Last seen on the firewall">now {{ p.prefix }}</span>
        </template>
        <button type="button" class="btn small ghost rm" aria-label="Remove prefix" @click="remove(i)"><X :size="14" /></button>
      </div>
      <div class="pfx-sub">
        <label>Gateway <input v-model.trim="p.gateway" class="mono" :placeholder="p.family === 4 ? '254 or full address' : '::1'" :class="{ invalid: err(i, 'gateway') }"></label>
        <label>DHCP <input v-model.trim="p.dhcp_start" class="mono" :placeholder="p.family === 4 ? '50' : '::1000'" :class="{ invalid: err(i, 'dhcp_start') }"></label>
        <span class="dash">–</span>
        <input v-model.trim="p.dhcp_end" class="mono" :placeholder="p.family === 4 ? '199' : '::1fff'" aria-label="DHCP end" :class="{ invalid: err(i, 'dhcp_end') }">
      </div>
      <input v-model="p.description" class="desc" placeholder="Description (ULA, GUA, …)" aria-label="Description">
      <div v-for="k in ['family', 'prefix', 'source', 'delegated_from', 'prefix_id', 'gateway', 'dhcp_start', 'dhcp_end']" :key="k">
        <div v-if="err(i, k)" class="err">{{ err(i, k) }}</div>
      </div>
    </div>
    <p v-if="!prefixes.length" class="faint small" style="margin: 0">No addresses yet. A network without prefixes is fine for tunnels and external ranges.</p>
    <p class="hint">Gateway and DHCP range: a full address or a suffix added to the prefix (254 → .254, ::1). A delegated IPv6 prefix comes from the firewall: the prefix it receives on the WAN interface plus the prefix ID (often the VLAN, in hex).</p>
  </div>
</template>

<style scoped>
.pfx-row { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
.pfx-row.bad { border-color: var(--err); }
.pfx-top, .pfx-sub { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.pfx-sub { margin-top: 6px; font-size: 12.5px; color: var(--faint); }
.pfx-sub label { display: flex; gap: 6px; align-items: center; }
.pfx-row input { width: auto !important; padding: 3px 8px !important; font-size: 12.5px !important; }
.fam { min-width: 70px; } .src { min-width: 96px; } .len { min-width: 64px; }
.addr { flex: 1 1 160px; min-width: 120px; width: 160px !important; }
.pfx-row input.short { width: 72px !important; }
.pfx-row .addr { flex: 1 1 150px; width: 150px !important; }
.pfx-row .rm { margin-left: auto; }
.pfx-sub input { width: 110px !important; }
.pfx-row .desc { display: block; width: 100% !important; margin-top: 6px; }
.dash { color: var(--faint); font-family: var(--mono); }
</style>
