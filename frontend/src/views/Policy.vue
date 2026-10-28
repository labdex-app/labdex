<script setup>
// The zone matrix: which zone may reach which, on which ports. No cell = no access.
// Its second tab: how each firewall that enforces it matches it.
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PolicyFirewalls from '../components/PolicyFirewalls.vue'
import { api, ApiError } from '../api'
import ZoneChip from '../components/ZoneChip.vue'
import ChipInput from '../components/ChipInput.vue'
import Field from '../components/Field.vue'
import Modal from '../components/Modal.vue'
import { useQuery } from '../query'
import { toast } from '../store'

const route = useRoute()
const router = useRouter()
const tab = computed(() => (route.query.tab === 'firewalls' ? 'firewalls' : 'matrix'))
const setTab = (t) => router.replace({ query: { ...route.query, tab: t === 'matrix' ? undefined : t } })
const q = useQuery('/api/zone-policy', { onError: (e) => toast(e.message, 'error') })
const d = computed(() => q.data.value)
const zones = computed(() => d.value?.zones || [])
const cell = (s, t) => (d.value?.cells || []).find((c) => c.src === s && c.dst === t)
const edit = ref(null)
const errors = ref({})
const busy = ref(false)

function open(s, t) {
  const c = cell(s, t)
  errors.value = {}
  edit.value = { src: s, dst: t, ports: [...(c?.ports || [])], description: c?.description || '' }
}
const portSugg = computed(() => [{ value: 'any', label: 'every port', group: '' }, ...(d.value?.port_aliases || []).map((a) => ({ value: a, label: '', group: 'port alias' }))])
async function save() {
  busy.value = true
  errors.value = {}
  try {
    await api.put('/api/zone-policy', edit.value)
    edit.value = null
    await q.refresh()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = Object.fromEntries(e.errors.map((x) => [x.field, x.message]))
    else toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}
const short = (c) => (c.ports.includes('any') ? 'any' : c.ports.length <= 2 ? c.ports.join(' ') : `${c.ports.slice(0, 2).join(' ')} +${c.ports.length - 2}`)
const count = computed(() => (d.value?.cells || []).length)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Zone policy</h1>
      <span class="sub">{{ count }} allowed zone pair{{ count === 1 ? '' : 's' }}. Anything not listed is denied.</span>
    </div>
    <nav class="tabs" aria-label="Policy">
      <a href="#" :class="{ 'router-link-active': tab === 'matrix' }" :aria-current="tab === 'matrix' ? 'page' : undefined" @click.prevent="setTab('matrix')">Matrix</a>
      <a href="#" :class="{ 'router-link-active': tab === 'firewalls' }" :aria-current="tab === 'firewalls' ? 'page' : undefined" @click.prevent="setTab('firewalls')">
        On the firewalls <span v-if="d" class="note">{{ d.firewalls.length }}</span></a>
    </nav>
    <PolicyFirewalls v-if="tab === 'firewalls' && d" :firewalls="d.firewalls" />
    <div v-if="d && tab === 'matrix'" class="card flush">
      <div class="table-wrap" tabindex="0" role="region" aria-label="Zone policy matrix">
        <table class="matrix">
          <thead>
            <tr><th class="corner"><span class="faint small">from ↓ to →</span></th>
              <th v-for="z in zones" :key="z.code" class="zh"><ZoneChip :zone="z.code" /></th></tr>
          </thead>
          <tbody>
            <tr v-for="s in zones" :key="s.code">
              <th class="zr"><ZoneChip :zone="s.code" /><div class="small faint">{{ s.long }}</div></th>
              <td v-for="t in zones" :key="t.code" class="cellc" :class="{ on: cell(s.code, t.code), self: s.code === t.code, sel: edit?.src === s.code && edit?.dst === t.code, ro: !d.can_edit }"
                  :title="cell(s.code, t.code)?.description || (cell(s.code, t.code) ? '' : `${s.code} → ${t.code}: no access`)"
                  @click="d.can_edit && open(s.code, t.code)">
                <span v-if="cell(s.code, t.code)" class="mono small">{{ short(cell(s.code, t.code)) }}</span>
                <span v-else class="faint">·</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <Modal v-if="edit" :title="`${edit.src} → ${edit.dst}`" :busy="busy" @close="edit = null">
      <template #head><span class="small muted">{{ edit.src === edit.dst ? 'between networks of the same zone' : '' }}</span></template>
      <form id="cell-form" @submit.prevent="save">
        <div class="pair"><ZoneChip :zone="edit.src" /><span class="faint">may reach</span><ZoneChip :zone="edit.dst" /></div>
        <Field label="Ports" :error="errors.ports" hint="No ports = no access. Each cell becomes one firewall rule (category automation:labdex), as a plan." class="mt-4">
          <ChipInput v-model="edit.ports" :suggestions="portSugg" mono :max="40" placeholder="any, SG_WEB, tcp/443, udp/53" label="Add ports" />
        </Field>
        <Field label="Why" class="mt-4"><input v-model="edit.description" type="text" placeholder="Shown in the rule's description"></Field>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="edit = null">Cancel</button>
        <button form="cell-form" class="btn primary" :disabled="busy">{{ edit.ports.length ? 'Save' : 'Save: no access' }}</button>
      </template>
    </Modal>
    <p v-if="tab === 'matrix'" class="hint">Rows are where traffic comes from, columns where it goes. A service's “allow from” adds access for that one service on top.</p>
  </div>
</template>

<style scoped>
.matrix th, .matrix td { text-align: center; }
.matrix .corner { text-align: left; }
.matrix .zr { text-align: left; white-space: nowrap; }
.cellc { cursor: pointer; min-width: 74px; border-left: 1px solid var(--line); }
.cellc.ro { cursor: default; }
.cellc:hover { background: var(--raise); }
.cellc.on { background: rgba(74, 222, 128, .08); }
.cellc.self:not(.on) { background: rgba(255, 255, 255, .02); }
.cellc.sel { outline: 2px solid var(--accent); outline-offset: -2px; }
.pair { display: flex; align-items: center; gap: 10px; }
</style>
