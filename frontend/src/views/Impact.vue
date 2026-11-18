<script setup>
// What breaks if these hosts go down: for a failure, or before planned maintenance.
// Everything comes from the inventory (runs-on, cables through patch panels, gateways, VIPs,
// services); each affected host says why.
import { CircleAlert, Unplug, X } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import HostPicker from '../components/HostPicker.vue'

const route = useRoute()
const router = useRouter()
const chosen = computed(() => [].concat(route.query.host || []).filter(Boolean))
const res = ref(null)
const busy = ref(false)
const error = ref('')
const showUnknown = ref(false)

function setHosts(list) { router.replace({ query: { ...route.query, host: list.length ? list : undefined } }) }
const add = (name) => { if (!chosen.value.includes(name)) setHosts([...chosen.value, name]) }
const drop = (name) => setHosts(chosen.value.filter((n) => n !== name))

async function load() {
  error.value = ''
  if (!chosen.value.length) { res.value = null; return }
  busy.value = true
  try {
    res.value = await api.get('/api/impact?' + chosen.value.map((n) => `host=${encodeURIComponent(n)}`).join('&'))
  } catch (e) { error.value = e.message; res.value = null } finally { busy.value = false }
}
watch(chosen, load, { immediate: true })

const others = (rows) => rows.filter((r) => r.because !== 'chosen')
const nothing = computed(() => res.value && !others(res.value.down).length && !res.value.cut_off.length && !res.value.no_routing.length
  && !res.value.vips.length && !res.value.dns)
const hostLink = (r) => `/hosts/${r.uid || r.host}`
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Impact</h1>
      <span class="sub">What breaks if these go down: after a failure, or before you take something down for maintenance.</span>
    </div>

    <div class="card pick">
      <span class="small muted">If these go down:</span>
      <span v-for="n in chosen" :key="n" class="chip mono">{{ n }}
        <button class="x" :aria-label="`Remove ${n}`" @click="drop(n)"><X :size="12" /></button></span>
      <HostPicker all :exclude="chosen" placeholder="Add a host, switch, hypervisor…" style="min-width: 240px" @pick="add" />
    </div>

    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="!chosen.length" class="muted empty">Pick a host above. labdex follows what runs on it, the cables through switches and patch panels,
      the networks' gateways, VIPs and services, and says for every affected host why.</p>
    <p v-else-if="busy && !res" class="muted">Working it out…</p>

    <template v-if="res">
      <div class="stats" :class="{ dim: busy }">
        <div class="stat" :class="{ bad: others(res.down).length }"><b>{{ others(res.down).length }}</b><span>go down with it</span></div>
        <div class="stat" :class="{ bad: res.cut_off.length }"><b>{{ res.cut_off.length }}</b><span>cut off</span></div>
        <div class="stat" :class="{ warn: res.no_routing.length }"><b>{{ res.no_routing.length }}</b><span>networks without routing</span></div>
        <div class="stat" :class="{ warn: res.services.length }"><b>{{ res.services.length }}</b><span>services unavailable</span></div>
      </div>

      <p v-if="res.dns" class="banner" :class="res.dns.state === 'down' ? 'errbox' : 'warnbox'">
        <CircleAlert :size="15" />
        <template v-if="res.dns.state === 'down'"><b>Name resolution stops:</b> every DNS server is affected ({{ res.dns.affected.join(', ') }}).</template>
        <template v-else><b>Fewer DNS servers:</b> {{ res.dns.affected.join(', ') }} affected; {{ res.dns.left.join(', ') }} still answer.</template>
      </p>
      <p v-if="nothing" class="small ok-note">Nothing else depends on {{ chosen.length === 1 ? 'it' : 'them' }}, as far as the inventory knows.</p>
      <p v-if="!res.gateways_known" class="small muted">No network has a gateway that belongs to a host here, so labdex can't tell what gets cut off;
        set the networks' gateways and the hosts that hold them.</p>

      <section v-if="others(res.down).length" class="card">
        <h2>Go down with {{ chosen.length === 1 ? 'it' : 'them' }}</h2>
        <table class="small">
          <thead><tr><th>Host</th><th class="hide-m">Function</th><th>Why</th></tr></thead>
          <tbody><tr v-for="r in others(res.down)" :key="r.host">
            <td><RouterLink :to="hostLink(r)" class="mono">{{ r.host }}</RouterLink> <span v-if="r.fun" class="fun faint">{{ r.fun }}</span></td>
            <td class="hide-m muted">{{ r.function }}</td><td>{{ r.because }}</td></tr></tbody>
        </table>
      </section>

      <section v-if="res.cut_off.length" class="card">
        <h2><Unplug :size="16" /> Cut off</h2>
        <p class="small muted">Still running, but no cable path to a gateway is left.</p>
        <table class="small">
          <thead><tr><th>Host</th><th class="hide-m">Function</th><th>Why</th></tr></thead>
          <tbody><tr v-for="r in res.cut_off" :key="r.host">
            <td><RouterLink :to="hostLink(r)" class="mono">{{ r.host }}</RouterLink> <span v-if="r.fun" class="fun faint">{{ r.fun }}</span></td>
            <td class="hide-m muted">{{ r.function }}</td><td>{{ r.because }}</td></tr></tbody>
        </table>
      </section>

      <section v-if="res.no_routing.length" class="card">
        <h2>Networks without routing</h2>
        <p class="small muted">Hosts in these networks still reach each other, but nothing beyond.</p>
        <table class="small">
          <thead><tr><th>Network</th><th>Why</th><th>Hosts in it</th></tr></thead>
          <tbody><tr v-for="n in res.no_routing" :key="n.network">
            <td><RouterLink :to="`/networks/${n.uid || n.network}`" class="mono">{{ n.network }}</RouterLink></td>
            <td>{{ n.because }}</td>
            <td><span class="muted">{{ n.hosts.length }}</span><span v-if="n.hosts.length" class="faint hide-m">: {{ n.hosts.slice(0, 6).join(', ') }}{{ n.hosts.length > 6 ? ' …' : '' }}</span></td>
          </tr></tbody>
        </table>
      </section>

      <section v-if="res.vips.length" class="card">
        <h2>Shared addresses (VIPs)</h2>
        <table class="small">
          <thead><tr><th>VIP</th><th>State</th><th>Members</th></tr></thead>
          <tbody><tr v-for="v in res.vips" :key="v.host">
            <td><RouterLink :to="`/hosts/${v.uid || v.host}`" class="mono">{{ v.host }}</RouterLink></td>
            <td><span class="pill" :class="{ warn: v.state !== 'down' }">{{ v.state }}</span></td>
            <td>{{ v.members_affected.join(', ') }} affected<template v-if="v.members_left.length">; {{ v.members_left.join(', ') }} carry on</template></td>
          </tr></tbody>
        </table>
      </section>

      <section v-if="res.services.length" class="card">
        <h2>Services unavailable</h2>
        <table class="small">
          <thead><tr><th>Service</th><th>On</th><th class="hide-m">Port</th></tr></thead>
          <tbody><tr v-for="s in res.services" :key="`${s.host}/${s.name}`">
            <td><a v-if="s.url" :href="s.url" target="_blank" rel="noopener">{{ s.name }}</a><span v-else>{{ s.name }}</span>
              <span v-if="s.description" class="faint hide-m"> · {{ s.description }}</span></td>
            <td><RouterLink :to="`/hosts/${s.uid || s.host}`" class="mono">{{ s.host }}</RouterLink></td>
            <td class="hide-m mono">{{ s.port ? `${s.port}/${s.proto}` : '—' }}</td>
          </tr></tbody>
        </table>
      </section>

      <p v-if="res.unknown.length" class="small muted">
        {{ res.unknown.length }} host{{ res.unknown.length === 1 ? ' has' : 's have' }} no cable on record, so labdex can't tell whether
        {{ res.unknown.length === 1 ? 'it is' : 'they are' }} affected.
        <a href="#" @click.prevent="showUnknown = !showUnknown">{{ showUnknown ? 'Hide' : 'Show' }}</a>
        <span v-if="showUnknown" class="mono faint"><br>{{ res.unknown.join(', ') }}</span>
      </p>
      <p v-if="res.hidden" class="small faint">{{ res.hidden }} more affected host{{ res.hidden === 1 ? '' : 's' }} you can't see.</p>
      <p v-if="res.unknown_names.length" class="small err">No host called {{ res.unknown_names.join(', ') }}.</p>
    </template>
  </div>
</template>

<style scoped>
.pick { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 4px 3px 9px; border-radius: 999px; background: var(--accent-faint); font-size: 13px; }
.chip .x { border: 0; background: none; padding: 2px; cursor: pointer; color: inherit; opacity: 0.6; display: inline-flex; }
.chip .x:hover { opacity: 1; }
.empty { max-width: 640px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 16px; }
.stats.dim { opacity: 0.6; }
.stat { border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; display: flex; flex-direction: column; }
.stat b { font-size: 24px; font-variant-numeric: tabular-nums; }
.stat span { font-size: 12px; color: var(--muted); }
.stat.bad b { color: var(--err); }
.stat.warn b { color: var(--warn); }
.banner { display: flex; align-items: center; gap: 8px; }
section.card { margin-bottom: 14px; }
section h2 { font-size: 15px; margin: 0 0 6px; display: flex; align-items: center; gap: 6px; }
section p { margin: 0 0 8px; }
.fun { font-size: 12px; }
.ok-note { color: var(--ok); }
</style>
