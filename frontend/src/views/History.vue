<script setup>
import FilterSelect from '../components/FilterSelect.vue'
import { computed, onActivated, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { ago, stamp, toast } from '../store'
import Diff from '../components/Diff.vue'
import Loading from '../components/Loading.vue'

const route = useRoute()
const router = useRouter()
const TYPES = ['host', 'network', 'alias', 'zone', 'area', 'zone_policy', 'tag', 'feature', 'hostmodule', 'integration', 'inventory', 'setting', 'user', 'group', 'role', 'binding', 'grant', 'token']
const ACTIONS = ['create', 'update', 'delete', 'import', 'revert', 'apply', 'login']
const f = ref({
  object_type: route.query.object_type || '', object_name: route.query.object_name || '',
  action: route.query.action || '', actor: route.query.actor || '',
})
const events = ref([])
const more = ref(false)
const loading = ref(false)
const PAGE = 50

// "What changed since …": the net change per object instead of the list of events
const net = ref(route.query.since ? route.query.since : '')
const SINCE = [{ value: '24h', label: 'the last day' }, { value: '7d', label: 'the last week' }, { value: '30d', label: 'the last 30 days' }]
const netData = ref(null)
const open = ref(new Set())
async function loadNet() {
  if (!net.value) { netData.value = null; return }
  loading.value = true
  try {
    netData.value = await api.get(`/api/history/changes?since=${encodeURIComponent(net.value)}`)
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}
function setNet(v) {
  net.value = v || ''
  router.replace({ query: net.value ? { since: net.value } : {} })
  if (net.value) loadNet(); else load()
}
const sign = { created: '+', updated: '~', deleted: '−' }
const toggle = (k) => { const o = new Set(open.value); if (o.has(k)) o.delete(k); else o.add(k); open.value = o }

const qs = computed(() => new URLSearchParams(Object.entries(f.value).filter(([, v]) => v)).toString())
// sign-ins are history too, but they drown the changes: shown on request
const logins = ref(route.query.logins === '1')
const extra = computed(() => (logins.value || f.value.action === 'login' ? '' : '&changes=1'))

async function load(append = false) {
  loading.value = true
  try {
    const before = append && events.value.length ? `&before=${events.value[events.value.length - 1].id}` : ''
    const rows = await api.get(`/api/history?limit=${PAGE}&${qs.value}${extra.value}${before}`)
    events.value = append ? [...events.value, ...rows] : rows
    more.value = rows.length === PAGE
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}
onMounted(() => (net.value ? loadNet() : load()))
let shown = false
onActivated(() => { if (shown) (net.value ? loadNet() : load()); shown = true })

function apply() {
  router.replace({ query: Object.fromEntries(Object.entries(f.value).filter(([, v]) => v)) })
  load()
}

const link = (e) => ({ host: `/hosts/${e.object_name}`, network: `/networks/${e.object_name}`, integration: `/integrations/${e.object_name}` }[e.object_type])
</script>

<template>
  <div class="page">
    <div class="page-head"><h1>History</h1><span class="sub">Every change, who made it, and the way back.</span></div>

    <div class="filters">
      <FilterSelect :model-value="net" label="Net change since" :options="SINCE" @update:model-value="setNet" />
    </div>

    <div v-if="net && netData" class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th /><th>Object</th><th>Change</th><th>Edits</th><th>Who</th></tr></thead>
          <tbody>
            <template v-for="o in netData.objects" :key="o.object_type + o.object_name">
              <tr :class="{ click: o.diff.length }" @click="o.diff.length && toggle(o.object_type + o.object_name)">
                <td class="mono">{{ sign[o.change] }}</td>
                <td class="nowrap"><span class="faint small" style="margin-right: 6px">{{ o.object_type }}</span><span class="mono">{{ o.object_name }}</span></td>
                <td class="small">{{ o.change }}<span v-if="o.fields.length" class="faint"> · {{ o.fields.slice(0, 4).join(', ') }}{{ o.fields.length > 4 ? ', …' : '' }}</span></td>
                <td class="small">{{ o.events }}</td>
                <td class="small">{{ o.actors.join(', ') }}</td>
              </tr>
              <tr v-if="open.has(o.object_type + o.object_name)"><td /><td colspan="4"><Diff :rows="o.diff" /></td></tr>
            </template>
            <tr v-if="!netData.objects.length && loading"><td colspan="5"><Loading /></td></tr>
            <tr v-else-if="!netData.objects.length"><td colspan="5" class="empty">Nothing changed in that time.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <form v-if="!net" class="filters" @submit.prevent="apply">
      <FilterSelect :model-value="f.object_type" label="Object" :options="TYPES" @update:model-value="(v) => { f.object_type = v || ''; apply() }" />
      <FilterSelect :model-value="f.action" label="Action" :options="ACTIONS" @update:model-value="(v) => { f.action = v || ''; apply() }" />
      <input v-model.trim="f.object_name" type="search" placeholder="Exact name" style="width: 200px" aria-label="Object name" @change="apply">
      <input v-model.trim="f.actor" type="search" placeholder="Who" style="width: 140px" aria-label="Actor" @change="apply">
      <label class="check small"><input v-model="logins" type="checkbox" @change="load()"> Sign-ins too</label>
      <button v-if="qs" type="button" class="btn small ghost" @click="f = { object_type: '', object_name: '', action: '', actor: '' }; apply()">Clear filters</button>
    </form>

    <div v-if="!net" class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Object</th><th>Change</th></tr></thead>
          <tbody>
            <tr v-for="e in events" :key="e.id" class="click" v-rowlink @click="router.push(`/history/${e.id}`)">
              <td class="small nowrap" :title="stamp(e.ts)">{{ ago(e.ts) }}</td>
              <td class="small">{{ e.actor }} <span v-if="e.actor_kind !== 'user'" class="tag">{{ e.actor_kind }}</span></td>
              <td><span class="op" :class="`op-${e.action}`">{{ e.action }}</span></td>
              <td class="nowrap"><span class="faint small" style="margin-right: 6px">{{ e.object_type }}</span>
                <RouterLink v-if="link(e) && e.action !== 'delete'" :to="link(e)" class="mono" @click.stop> {{ e.object_name }}</RouterLink>
                <span v-else class="mono"> {{ e.object_name }}</span></td>
              <td class="small">{{ e.summary }}</td>
            </tr>
            <tr v-if="!events.length && loading"><td colspan="5"><Loading /></td></tr>
            <tr v-else-if="!events.length"><td colspan="5" class="empty">No changes match.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div v-if="more && !net" style="text-align: center; margin-top: 14px"><button class="btn" :disabled="loading" @click="load(true)">Older</button></div>
  </div>
</template>
