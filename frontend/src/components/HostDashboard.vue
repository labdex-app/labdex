<script setup>
// A host at a glance: status and what monitors say, addresses, where it sits, links to its
// services, and what plugins show for it. The landing tab of a host.
import Copy from './Copy.vue'
import PatchState from './PatchState.vue'
import { ago, stamp } from '../store'
import PortMap from './PortMap.vue'
import WidgetGrid from './WidgetGrid.vue'
import ZoneChip from './ZoneChip.vue'
import HistoryList from './HistoryList.vue'
import HealthStrip from './HealthStrip.vue'
import HostGuests from './HostGuests.vue'

const props = defineProps({ host: { type: Object, required: true } })
const emit = defineEmits(['tab'])
const gb = (b) => (b >= 2 ** 40 ? `${(b / 2 ** 40).toFixed(1).replace(/\.0$/, '')} TB` : `${Math.round(b / 2 ** 30)} GB`)
const u = () => props.host.usage || {}
</script>

<template>
  <div class="hostdash">
    <!-- the numbers: use from the host's agent, updates, and how reachable it was -->
    <section class="card flush hstats">
      <div class="cells">
        <div class="cell"><span class="label">CPU</span>
          <b v-if="u().cpu != null">{{ Math.round(u().cpu) }}<small>%</small></b><b v-else class="faint">—</b>
          <span v-if="u().cpus" class="small faint">{{ u().cpus }} cores</span></div>
        <div class="cell"><span class="label">Memory</span>
          <b v-if="u().mem != null">{{ Math.round(u().mem) }}<small>%</small></b><b v-else class="faint">—</b>
          <span v-if="u().mem_total" class="small faint">of {{ gb(u().mem_total) }}</span></div>
        <div class="cell"><span class="label">Disk</span>
          <b v-if="u().disk != null">{{ Math.round(u().disk) }}<small>%</small></b><b v-else class="faint">—</b>
          <span v-if="u().disk_total" class="small faint">of {{ gb(u().disk_total) }} on /</span></div>
        <div class="cell"><span class="label">Updates</span>
          <b>{{ host.patches?.state === 'unknown' ? '—' : host.patches?.pending || 0 }}</b>
          <span v-if="host.patches?.security" class="small c-warn">{{ host.patches.security }} security</span></div>
        <div class="cell"><span class="label">Reachable</span>
          <b v-if="host.health?.up_pct != null">{{ host.health.up_pct }}<small>%</small></b><b v-else class="faint">—</b>
          <span class="small faint">48 h</span></div>
      </div>
      <div class="health">
        <div class="h-head"><span class="label">Health · 48 h</span><span class="grow"></span><span class="label">now</span></div>
        <HealthStrip v-if="host.health?.hours?.length" :hours="host.health.hours" />
        <p v-else class="small faint m-0">No history yet: it starts when a monitoring plugin reports this host.</p>
      </div>
      <p v-if="!host.usage" class="small faint m-0 note">CPU, memory and disk come from the host's agent.
        <a href="#" @click.prevent="emit('tab', 'agent')">{{ host.agent ? 'Its agent isn\'t linked right now.' : 'Link one' }}</a></p>
    </section>

    <section v-if="host.guests?.length" class="card flush mb-4">
      <div class="sect-head pad"><h2>Guests</h2><span class="small faint">{{ host.guests.length }} on this host</span><span class="grow"></span>
        <a v-if="host.guests.length > 5" href="#" @click.prevent="emit('tab', 'guests')">All {{ host.guests.length }}</a></div>
      <HostGuests :host="host.name" :limit="5" />
    </section>

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
      <div v-if="host.patches" class="card">
        <h3>Patches</h3>
        <div class="row" style="gap: 8px"><PatchState :state="host.patches" /></div>
        <dl v-if="host.patches.state !== 'unknown'" class="kv small mt-2">
          <dt>updates</dt><dd>{{ host.patches.pending }}<span v-if="host.patches.security" class="faint">, {{ host.patches.security }} security</span></dd>
          <template v-if="host.patches.os"><dt>os</dt><dd>{{ host.patches.os }}</dd></template>
          <template v-if="host.patches.kernel"><dt>kernel</dt><dd class="mono">{{ host.patches.kernel }}</dd></template>
          <dt>patched</dt><dd :title="stamp(host.patches.patched_at)">{{ ago(host.patches.patched_at) }}</dd>
          <dt>reported</dt><dd :title="stamp(host.patches.checked_at)">{{ ago(host.patches.checked_at) }} by {{ host.patches.sources.map((s) => s.source).join(', ') }}</dd>
        </dl>
        <p v-else class="small faint m-0 mt-2">Nothing reports updates for this host. <RouterLink to="/patches">How</RouterLink></p>
      </div>
      <div class="card">
        <h3>Details</h3>
        <dl class="kv small">
          <template v-if="host.function"><dt>function</dt><dd>{{ host.function }}</dd></template>
          <template v-if="host.vendor || host.model"><dt>model</dt><dd>{{ [host.vendor, host.model].filter(Boolean).join(' ') }}</dd></template>
          <template v-if="host.patches?.os"><dt>os</dt><dd>{{ host.patches.os }}</dd></template>
          <template v-if="host.agent"><dt>agent</dt><dd class="mono">{{ host.agent.version || 'enrolled' }}{{ host.agent.mtls ? ' · mTLS' : '' }}{{ host.agent.linked ? '' : ' · not linked' }}</dd></template>
          <template v-if="host.features?.length"><dt>features</dt><dd class="mono">{{ host.features.join(', ') }}</dd></template>
          <template v-if="host.tags?.length"><dt>tags</dt><dd>{{ host.tags.join(', ') }}</dd></template>
          <dt>status</dt><dd>{{ host.status }}</dd>
        </dl>
      </div>
      <HistoryList type="host" :name="host.name" :limit="4" title="Activity" />
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
.hstats { margin: 0 0 16px; }
.c-warn { color: var(--warn); }
.cells { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.cell { display: flex; flex-direction: column; gap: 4px; padding: 14px 18px; border-left: 1px solid var(--line); min-width: 0; }
.cell:first-child { border-left: 0; }
.cell b { font: 500 calc(22px * var(--text))/1.2 var(--mono); }
.cell b small { font-size: calc(12px * var(--text)); color: var(--muted); font-weight: 400; margin-left: 2px; }
.health { padding: 12px 18px 14px; }
.h-head { display: flex; margin-bottom: 8px; }
.note { padding: 0 18px 12px; }
.pad { padding: 14px 18px; margin: 0; }
@media (max-width: 760px) { .cells { grid-template-columns: repeat(2, minmax(0, 1fr)); } .cell:nth-child(odd) { border-left: 0; } .cell { border-top: 1px solid var(--line); } }
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
