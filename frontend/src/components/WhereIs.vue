<script setup>
// Where is this MAC? Which switch port (or access point) a MAC, an IP address or a host's
// interfaces were seen on, from the switches' forwarding tables (SNMP, UniFi).
import { MapPin, Search } from 'lucide-vue-next'
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { ago } from '../store'

const route = useRoute()
const router = useRouter()
const q = ref(String(route.query.q || ''))
const res = ref(null)
const busy = ref(false)
const error = ref('')
async function find() {
  const v = q.value.trim()
  if (!v) { res.value = null; return }
  busy.value = true
  error.value = ''
  router.replace({ query: { ...route.query, tab: 'where', q: v } })
  try { res.value = await api.get(`/api/discovery/where?q=${encodeURIComponent(v)}`) } catch (e) { error.value = e.message } finally { busy.value = false }
}
onMounted(() => { if (q.value) find() })
watch(() => route.query.q, (v) => { if (v && v !== q.value) { q.value = String(v); find() } })
</script>

<template>
  <div class="where">
    <form class="row ask" @submit.prevent="find">
      <input v-model="q" type="search" class="mono" placeholder="aa:bb:cc:dd:ee:ff, 10.10.3.42 or a host" aria-label="MAC, IP address or host">
      <button class="btn primary" :disabled="busy || !q.trim()"><Search :size="15" /> Find</button>
    </form>
    <p v-if="error" class="err">{{ error }}</p>
    <template v-if="res">
      <p v-if="!res.sources.length" class="warnbox small">No switch has reported its MAC table yet. The <b>SNMP neighbours</b> plugin (1.1) reads them from your switches, the
        <b>UniFi Network</b> plugin (2.2) from the controller's client list; run their <i>Read</i> action once.</p>
      <p v-else-if="!res.results.length" class="small muted">Nothing known by that: not a MAC, no host or address with a MAC in the inventory, Discovery or a switch's client list.</p>
      <article v-for="r in res.results" :key="r.mac" class="card mres">
        <header>
          <span class="mono mac">{{ r.mac }}</span>
          <span v-if="r.vendor" class="small faint">{{ r.vendor }}</span>
          <span class="grow"></span>
          <span v-if="r.owner" class="small">belongs to <RouterLink :to="`/hosts/${r.owner.uid || r.owner.host}?tab=network`">{{ r.owner.host }}</RouterLink>
            <span v-if="r.owner.port" class="mono"> {{ r.owner.port }}</span></span>
          <span v-else-if="r.why" class="small faint">from {{ r.why }}</span>
        </header>
        <p v-if="!r.seen.length" class="small muted" style="margin: 6px 0 0">No switch has it in its table right now.</p>
        <table v-else class="small">
          <thead><tr><th>Switch</th><th>Port</th><th class="hide-m">VLAN</th><th class="hide-m">MACs on the port</th><th>Cabled to</th><th class="hide-m">Seen</th></tr></thead>
          <tbody>
            <tr v-for="(s, n) in r.seen" :key="n" :class="{ best: s.likely }">
              <td><MapPin v-if="s.likely" :size="13" class="pin" aria-label="most likely here" />
                <RouterLink v-if="s.known" :to="`/hosts/${s.uid || s.switch}?tab=network`">{{ s.switch }}</RouterLink><span v-else>{{ s.switch }}</span></td>
              <td class="mono">{{ s.port }}</td>
              <td class="hide-m">{{ s.vlan ?? '—' }}</td>
              <td class="hide-m">{{ s.macs_on_port }}<span v-if="s.uplink" class="faint"> · to another switch</span></td>
              <td><template v-if="s.cable"><RouterLink :to="`/hosts/${s.cable.host}?tab=network`">{{ s.cable.host }}</RouterLink> <span class="mono">{{ s.cable.port }}</span></template>
                <span v-else class="faint">—</span></td>
              <td class="hide-m faint">{{ s.source }} · {{ ago(s.seen_at) }}</td>
            </tr>
          </tbody>
        </table>
      </article>
      <p v-if="res.results.some((r) => r.seen.length > 1)" class="small faint">A MAC shows on its own port and on every link between switches on the way; the port with the fewest
        other MACs that isn't cabled to another switch is the likely one (pinned).</p>
    </template>
  </div>
</template>

<style scoped>
.ask { flex-wrap: nowrap; margin-bottom: 14px; }
.ask input { flex: 1; max-width: 420px; }
.mres { margin: 0 0 12px; }
.mres header { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.mac { font-weight: 600; font-size: calc(14px * var(--text)); }
tr.best td { background: var(--accent-faint); }
.pin { color: var(--accent-soft); margin-right: 4px; vertical-align: -2px; }
</style>
