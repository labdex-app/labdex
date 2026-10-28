<script setup>
// A host at a glance: status and what monitors say, addresses, where it sits, links to its
// services, and what plugins show for it. The landing tab of a host.
import Copy from './Copy.vue'
import PortMap from './PortMap.vue'
import WidgetGrid from './WidgetGrid.vue'
import ZoneChip from './ZoneChip.vue'

defineProps({ host: { type: Object, required: true } })
const emit = defineEmits(['tab'])
</script>

<template>
  <div class="hostdash">
    <div class="glance">
      <div class="card">
        <h3>Status</h3>
        <div class="row" style="gap: 8px"><span class="status" :class="`s-${host.status}`">{{ host.status }}</span>
          <span v-if="host.health" class="hstate" :class="host.health.live">{{ host.health.live === 'unknown' ? 'no live data' : host.health.live }}</span></div>
        <table v-if="host.health?.sources.length" class="mini">
          <tbody><tr v-for="x in host.health.sources" :key="x.integration">
            <td><RouterLink :to="`/integrations/${x.integration}`" class="mono">{{ x.integration }}</RouterLink></td>
            <td><span class="hstate" :class="x.state">{{ x.state }}</span></td><td class="small faint">{{ x.detail }}</td></tr></tbody>
        </table>
        <p v-else class="small faint m-0 mt-2">{{ host.health?.discovery ? 'Seen by Discovery; no monitor reports this host.' : 'No monitor reports this host.' }}</p>
      </div>
      <div class="card">
        <h3>Addresses</h3>
        <div v-for="(i, n) in host.interfaces.filter((x) => x.ip4 || x.ip6 || (x.extra_ips || []).length)" :key="n" class="addr">
          <span class="faint small">{{ i.label || i.network }}</span>
          <span class="addrs"><Copy v-for="a in [i.ip4, i.ip6, ...(i.extra_ips || [])].filter(Boolean)" :key="a" :text="a" /></span>
        </div>
        <p v-if="!host.interfaces.some((x) => x.ip4 || x.ip6)" class="small faint m-0">No addresses.</p>
      </div>
      <div class="card">
        <h3>Where</h3>
        <dl class="kv small">
          <dt>zone</dt><dd><ZoneChip :zone="host.zone" /></dd>
          <template v-if="host.runs_on"><dt>runs on</dt><dd><RouterLink :to="`/hosts/${host.runs_on}`" class="mono">{{ host.runs_on }}</RouterLink></dd></template>
          <template v-if="host.guests?.length"><dt>guests</dt><dd class="guests">
            <RouterLink v-for="g in host.guests.slice(0, 8)" :key="g" :to="`/hosts/${g}`" class="mono">{{ g }}</RouterLink>
            <span v-if="host.guests.length > 8" class="faint">and {{ host.guests.length - 8 }} more</span></dd></template>
          <template v-if="host.cables?.length"><dt>cables</dt><dd>{{ host.cables.length }} <a href="#" @click.prevent="emit('tab', 'network')">show</a></dd></template>
          <template v-if="host.rack"><dt>rack</dt><dd><RouterLink :to="`/racks?rack=${host.rack_uid}`">{{ host.rack }}</RouterLink>
            <span v-if="host.rack_unit" class="mono"> U{{ host.rack_unit }}{{ host.rack_height > 1 ? `–${host.rack_unit + host.rack_height - 1}` : '' }}</span>
            <span v-if="host.rack_face !== 'front'" class="faint"> {{ host.rack_face === 'full' ? 'full depth' : 'rear' }}</span></dd></template>
          <template v-if="host.hardware"><dt>hardware</dt><dd>{{ host.hardware }}</dd></template>
        </dl>
      </div>
      <div v-if="host.links?.length" class="card">
        <h3>Open</h3>
        <div class="links"><a v-for="l in host.links" :key="l.url" :href="l.url" target="_blank" rel="noopener" class="btn small">{{ l.name }}</a></div>
      </div>
    </div>
    <PortMap :interfaces="host.interfaces || []" :cables="host.cables || []" @pick="emit('tab', 'network')" />
    <WidgetGrid :url="`/api/hosts/${host.name}/widgets`" title="From plugins" />
  </div>
</template>

<style scoped>
.glance { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin-bottom: 16px; }
.glance .card { margin: 0; min-width: 0; }
.glance h3 { margin-top: 0; }
.mini { margin-top: 8px; }
.mini td { padding: 4px 6px; }
.addr { display: flex; gap: 10px; align-items: baseline; padding: 3px 0; flex-wrap: wrap; }
.addr .addrs { display: flex; gap: 4px 10px; flex-wrap: wrap; }
.links { display: flex; gap: 6px; flex-wrap: wrap; }
.guests { display: flex; flex-direction: column; gap: 2px; }
</style>
