<script setup>
// One service, edited in a dialog: what listens where, how to link to it, who should reach it.
import { computed } from 'vue'
import { useQuery } from '../query'
import ChipInput from './ChipInput.vue'
import Field from './Field.vue'

// v-model:s: the service, edited in place; allow_from is a list here
const s = defineModel('s', { type: Object, required: true })
const props = defineProps({
  interfaces: { type: Array, default: () => [] },
  errors: { type: Object, default: () => ({}) },
  prefix: { type: String, default: '' }, // "services[3]" for server-side errors
  hostZone: { type: String, default: '' },
})

const src = useQuery('/api/services/sources')
const sources = computed(() => src.data.value || { zones: [], areas: [], networks: [], aliases: [], generated: [], prefixes: [] })
const suggestions = computed(() => [
  { value: 'any', label: 'anywhere', group: '' },
  ...sources.value.zones.map((z) => ({ value: z, label: '', group: 'zone' })),
  ...sources.value.areas.map((a) => ({ value: a, label: 'every zone of the area', group: 'area' })),
  ...sources.value.networks.map((n) => ({ value: n, label: (sources.value.prefixes || []).filter((p) => p.network === n).map((p) => p.cidr).join(' '), group: 'network' })),
  ...(sources.value.prefixes || []).map((p) => ({ value: p.cidr, label: p.network, group: 'prefix' })),
  ...sources.value.aliases.map((a) => ({ value: a, label: '', group: 'alias' })),
  ...sources.value.generated.map((a) => ({ value: a, label: '', group: 'generated' })),
])
const known = computed(() => new Set(suggestions.value.map((x) => x.value)))
function unknown(t) {
  if (known.value.has(t) || /^[\d.]+(\/\d+)?$|:/.test(t)) return ''
  if (t.includes('*')) {
    const rx = new RegExp(`^${t.toUpperCase().replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`)
    if (sources.value.zones.some((z) => rx.test(z))) return ''
  }
  return 'Not a zone, network, alias or address labdex knows'
}
const err = (k) => props.errors[`${props.prefix}.${k}`]
const web = computed(() => s.value.scheme === 'http' || s.value.scheme === 'https')
const bindOptions = computed(() => {
  const nets = [...new Set(props.interfaces.map((i) => i.network).filter(Boolean))]
  const addrs = props.interfaces.flatMap((i) => [i.ip4, ...(i.ip6_all || (i.ip6 ? [i.ip6] : [])), ...(i.extra_ips || [])]).filter(Boolean)
  return { nets, addrs }
})
const fqdn = computed(() => {
  const d = s.value.dns_name || ''
  if (!d) return ''
  return d.endsWith('.') ? d.slice(0, -1) : `${d}.${props.hostZone}`
})
</script>

<template>
  <div class="sf">
    <div class="g3">
      <Field label="Name" :error="err('name')"><input v-model.trim="s.name" placeholder="grafana" :class="{ invalid: err('name') }"></Field>
      <Field label="Port" :error="err('port')"><input v-model="s.port" type="number" min="1" max="65535" placeholder="3000" :class="{ invalid: err('port') }"></Field>
      <Field label="Protocol"><select v-model="s.proto"><option>tcp</option><option>udp</option></select></Field>
    </div>
    <Field label="Description"><input v-model="s.description" placeholder="What it is, who uses it"></Field>
    <div class="g3">
      <Field label="Link">
        <select v-model="s.scheme"><option value="">no link</option><option>https</option><option>http</option><option>ssh</option></select></Field>
      <Field v-if="web" label="Path"><input v-model.trim="s.path" placeholder="/"></Field>
      <Field v-if="web" label="Link by"><select v-model="s.link_to"><option value="">name</option><option value="ip">address</option></select></Field>
    </div>
    <Field label="DNS name" :error="err('dns_name')" :hint="fqdn ? `Becomes ${fqdn}` : 'Relative to the host’s zone (grafana), or a full name ending in a dot (grafana.example.com.)'">
      <input v-model.trim="s.dns_name" class="mono" placeholder="grafana" :class="{ invalid: err('dns_name') }" spellcheck="false">
    </Field>
    <div class="g2">
      <Field label="Listens on" :error="err('bind')">
        <select v-model="s.bind" :class="{ invalid: err('bind') }">
          <option value="">every address</option>
          <option value="primary">the primary interface</option>
          <option v-for="net in bindOptions.nets" :key="'n' + net" :value="net">network {{ net }}</option>
          <option v-for="a in bindOptions.addrs" :key="'a' + a" :value="a">{{ a }}</option>
          <option v-if="s.bind && s.bind !== 'primary' && !bindOptions.nets.includes(s.bind) && !bindOptions.addrs.includes(s.bind)" :value="s.bind">{{ s.bind }}</option>
        </select>
      </Field>
      <div class="checks">
        <label class="check" :title="s.name ? `_${s.name}._${s.proto || 'tcp'}.${hostZone}` : ''"><input v-model="s.publish_srv" type="checkbox" :disabled="!s.port"> Publish an SRV record</label>
        <label class="check"><input v-model="s.public" type="checkbox" :disabled="!s.port"> Public (port forward, split DNS)</label>
      </div>
    </div>
    <div v-if="s.public" class="g2">
      <Field label="Public name" :error="err('public_name')"><input v-model.trim="s.public_name" class="mono" placeholder="photos.example.com" :class="{ invalid: err('public_name') }"></Field>
      <Field label="WAN port" :error="err('public_port')"><input v-model="s.public_port" type="number" min="1" max="65535" :placeholder="String(s.port || 'same')"></Field>
    </div>
    <Field label="Allow from" :error="err('allow_from')" hint="Zones, areas, networks, aliases, addresses or CIDRs that should reach it; the firewall check uses this">
      <ChipInput v-model="s.allow_from" :suggestions="suggestions" mono :warn="unknown" :max="20" placeholder="CA_ST, mgmt, 10.0.0.0/8" label="Add a source" />
    </Field>
  </div>
</template>

<style scoped>
.sf > * + * { margin-top: 14px; }
.g2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; align-items: end; }
.g3 { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 12px 14px; }
.checks { display: flex; flex-direction: column; gap: 6px; padding-bottom: 6px; }
@media (max-width: 640px) { .g2, .g3 { grid-template-columns: minmax(0, 1fr); } }
</style>
