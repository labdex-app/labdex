<script setup>
// Cables the switches see (LLDP, CDP, UniFi) and the inventory doesn't have: accept them, or say
// no once; a cable that contradicts one on record can replace it. Neighbours labdex doesn't know
// at all can become hosts.
import { Check, Plus, Replace, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { api } from '../api'
import { useQuery } from '../query'
import { toast } from '../store'

const emit = defineEmits(['changed'])
const q = useQuery('/api/cables/suggestions', { onError: (e) => toast(e.message, 'error') })
const d = computed(() => q.data.value)
const canEdit = computed(() => !!d.value?.can_edit)
const picked = ref(new Set())
const busy = ref(false)
const allPicked = computed(() => d.value?.suggested.length && d.value.suggested.every((x) => picked.value.has(x.key)))
function toggle(k) { const p = new Set(picked.value); if (p.has(k)) p.delete(k); else p.add(k); picked.value = p }
function toggleAll() { picked.value = allPicked.value ? new Set() : new Set(d.value.suggested.map((x) => x.key)) }
const port = (x) => x.b_port || (x.b_mac ? `the interface with ${x.b_mac}` : '?')

async function accept(keys, replace = false) {
  busy.value = true
  try {
    const r = await api.post('/api/cables/suggestions/accept', { keys, replace })
    toast(r.errors.length ? `${r.made.length} made, ${r.errors.length} not: ${r.errors[0].message}` : `${r.made.length} cable${r.made.length === 1 ? '' : 's'} made`,
      r.errors.length ? 'error' : 'ok')
    picked.value = new Set()
    q.refresh()
    emit('changed')
  } catch (x) {
    toast(x.errors?.[0]?.message || x.message, 'error')
  } finally { busy.value = false }
}
async function ignore(keys) {
  await api.post('/api/cables/suggestions/ignore', { keys })
  picked.value = new Set()
  q.refresh()
}
const newHost = (u) => `/hosts/new?${new URLSearchParams({ ...(u.ip ? { ip: u.ip } : {}), ...(u.mac ? { mac: u.mac } : {}), hint: [u.name, u.platform].filter(Boolean).join(' · ') })}`
</script>

<template>
  <div v-if="d">
    <p v-if="!d.suggested.length && !d.conflicts.length && !d.unknown.length" class="empty">
      Nothing to suggest. {{ d.documented ? `The ${d.documented} cable${d.documented === 1 ? '' : 's'} the switches see ${d.documented === 1 ? 'is' : 'are'} all on record.` : '' }}
      Switches tell labdex about their neighbours through the <b>SNMP neighbours</b> plugin (LLDP and CDP) or the <b>UniFi</b> plugin signed in with a local account.
    </p>

    <section v-if="d.suggested.length" class="tsec">
      <div class="sect-head">
        <h2>New</h2><span class="note">seen by a switch, not on record yet</span><span class="grow"></span>
        <template v-if="canEdit">
          <button class="btn small" :disabled="!picked.size || busy" @click="ignore([...picked])"><X :size="14" /> Ignore</button>
          <button class="btn small primary" :disabled="!picked.size || busy" @click="accept([...picked])"><Check :size="14" /> Add {{ picked.size || '' }}</button>
        </template>
      </div>
      <div class="card" style="padding: 0">
        <table>
          <thead><tr><th v-if="canEdit" style="width: 34px"><input type="checkbox" class="tick" :checked="allPicked" aria-label="Pick all" @change="toggleAll"></th>
            <th>From</th><th>To</th><th class="hide-m">Seen by</th></tr></thead>
          <tbody>
            <tr v-for="x in d.suggested" :key="x.key">
              <td v-if="canEdit"><input type="checkbox" class="tick" :checked="picked.has(x.key)" :aria-label="`Pick ${x.a_host} ${x.a_port} – ${x.b_host}`" @change="toggle(x.key)"></td>
              <td><RouterLink :to="`/hosts/${x.a_uid}?tab=network`">{{ x.a_host }}</RouterLink> <span class="mono small">{{ x.a_port }}</span></td>
              <td><RouterLink :to="`/hosts/${x.b_uid}?tab=network`">{{ x.b_host }}</RouterLink> <span class="mono small" :class="{ faint: !x.b_port }">{{ port(x) }}</span></td>
              <td class="hide-m small muted">{{ x.source }} · {{ x.protocol.toUpperCase() }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="d.conflicts.length" class="tsec">
      <div class="sect-head"><h2>Different from the record</h2><span class="note">a switch sees something else on these ports</span></div>
      <div class="list">
        <div v-for="x in d.conflicts" :key="x.key" class="li">
          <div class="li-main">
            <div class="li-title"><span><RouterLink :to="`/hosts/${x.a_uid}?tab=network`">{{ x.a_host }}</RouterLink> <span class="mono">{{ x.a_port }}</span></span>
              <span class="faint">sees</span><span><RouterLink :to="`/hosts/${x.b_uid}?tab=network`">{{ x.b_host }}</RouterLink> <span class="mono">{{ port(x) }}</span></span></div>
            <div class="li-sub"><span v-if="x.now.a">on record: {{ x.a_host }} {{ x.a_port }} goes to <span class="mono">{{ x.now.a }}</span></span>
              <span v-if="x.now.b">on record: {{ x.b_host }} {{ x.b_port }} goes to <span class="mono">{{ x.now.b }}</span></span>
              <span class="faint">{{ x.source }} · {{ x.protocol.toUpperCase() }}</span></div>
          </div>
          <div v-if="canEdit" class="li-actions">
            <button class="btn small" :disabled="busy" @click="ignore([x.key])"><X :size="14" /> Keep the record</button>
            <button class="btn small" :disabled="busy" @click="accept([x.key], true)"><Replace :size="14" /> Use what's seen</button>
          </div>
        </div>
      </div>
    </section>

    <section v-if="d.unknown.length" class="tsec">
      <div class="sect-head"><h2>Neighbours labdex doesn't know</h2><span class="note">add them as hosts, then their cables show up here</span></div>
      <div class="card" style="padding: 0">
        <table>
          <thead><tr><th>On</th><th>Neighbour</th><th class="hide-m">Its port</th><th class="hide-m">Address</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(u, n) in d.unknown" :key="n">
              <td>{{ u.host }} <span class="mono small">{{ u.port }}</span></td>
              <td>{{ u.name || '—' }}<div v-if="u.platform" class="small faint ell">{{ u.platform }}</div></td>
              <td class="hide-m mono small">{{ u.remote_port }}</td>
              <td class="hide-m mono small">{{ [u.ip, u.mac].filter(Boolean).join(' · ') }}</td>
              <td class="right"><RouterLink v-if="canEdit" :to="newHost(u)" class="btn small"><Plus :size="14" /> Host</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <p v-if="d.ignored || d.documented" class="hint">{{ d.documented }} seen cable{{ d.documented === 1 ? '' : 's' }} already on record{{ d.ignored ? `, ${d.ignored} ignored` : '' }}.</p>
  </div>
</template>
