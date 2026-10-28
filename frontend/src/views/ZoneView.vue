<script setup>
// One zone: what it is, the networks in it, the hosts at home there, and what the policy lets
// in and out. Addressed by its stable id, so the link survives a rename.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import ZoneChip from '../components/ZoneChip.vue'
import { toast } from '../store'

const props = defineProps({ id: { type: String, required: true } })
const route = useRoute()
const router = useRouter()
const z = ref(null)
const notFound = ref(false)

async function load() {
  notFound.value = false
  try {
    const d = await api.get(`/api/zones/zones/${encodeURIComponent(props.id)}`)
    if (d.uid && props.id !== d.uid) { router.replace({ path: `/zones/${d.uid}`, query: route.query }); return }
    z.value = d
    document.title = `${d.code} · Zone · labdex`
  } catch (e) {
    if (e.status === 404) notFound.value = true
    else toast(e.message, 'error')
  }
}
watch(() => props.id, load, { immediate: true })
const ports = (c) => (c.ports || []).join(', ')
const counts = computed(() => z.value && `${z.value.networks.length} network${z.value.networks.length === 1 ? '' : 's'}, ${z.value.hosts.length} host${z.value.hosts.length === 1 ? '' : 's'} at home here`)
</script>

<template>
  <div class="page">
    <div v-if="notFound" class="errbox">No zone “{{ id }}”. <RouterLink to="/zones">All zones</RouterLink></div>
    <template v-else-if="z">
      <div class="page-head">
        <RouterLink to="/zones" class="btn ghost small">← Zones</RouterLink>
        <h1><ZoneChip :zone="z.code" /></h1>
        <span class="sub">{{ z.name }}</span>
        <span class="grow"></span>
        <span class="small faint">{{ counts }}</span>
      </div>
      <p v-if="z.description" class="muted" style="margin-top: -6px">{{ z.description }}</p>
      <div class="split">
        <div>
          <div class="card flush">
            <div style="padding: 14px 18px 4px"><h2>Networks</h2></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Name</th><th>VLAN</th><th>Prefix</th></tr></thead>
                <tbody>
                  <tr v-for="n in z.networks" :key="n.name" v-rowlink class="click" @click="router.push(`/networks/${n.uid}`)">
                    <td class="mono">{{ n.name }}</td><td class="mono">{{ n.vlan ?? '—' }}</td><td class="mono">{{ n.prefix4 || n.prefix6 || '—' }}</td></tr>
                  <tr v-if="!z.networks.length"><td colspan="3" class="empty">No network in this zone.</td></tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="card flush">
            <div style="padding: 14px 18px 4px"><h2>Hosts</h2></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Name</th><th>Function</th><th>Status</th></tr></thead>
                <tbody>
                  <tr v-for="h in z.hosts" :key="h.name" v-rowlink class="click" @click="router.push(`/hosts/${h.uid}`)">
                    <td><div>{{ h.fun || '—' }}</div><div class="mono small muted">{{ h.name }}</div></td><td>{{ h.function }}</td>
                    <td><span class="status" :class="`s-${h.status}`">{{ h.status }}</span></td></tr>
                  <tr v-if="!z.hosts.length"><td colspan="3" class="empty">No host has this as its home zone.</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <aside>
          <div class="card">
            <h3>About</h3>
            <dl class="kv small">
              <dt>area</dt><dd><span class="mono">{{ z.area }}</span> {{ z.area_name }}</dd>
              <dt>rank</dt><dd>{{ z.rank ?? 'none (not checked)' }}</dd>
              <dt>kind</dt><dd>{{ z.default ? 'the area’s default zone' : 'a zone of its own' }}</dd>
            </dl>
            <p class="hint">Higher ranks are more trusted; a host never sits in a network ranked above its own zone.</p>
          </div>
          <div class="card">
            <h3>Policy</h3>
            <div class="small faint">May reach</div>
            <ul class="plain small">
              <li v-for="c in z.policy_out" :key="c.dst"><RouterLink to="/policy" class="mono">{{ c.dst }}</RouterLink> <span class="faint">{{ ports(c) }}</span></li>
              <li v-if="!z.policy_out.length" class="faint">nothing (deny unless listed)</li>
            </ul>
            <div class="small faint mt-2">Reached from</div>
            <ul class="plain small">
              <li v-for="c in z.policy_in" :key="c.src"><RouterLink to="/policy" class="mono">{{ c.src }}</RouterLink> <span class="faint">{{ ports(c) }}</span></li>
              <li v-if="!z.policy_in.length" class="faint">nowhere</li>
            </ul>
          </div>
          <RouterLink v-if="z.can_edit" to="/zones" class="btn small mt-1">Edit on the Zones page</RouterLink>
        </aside>
      </div>
    </template>
  </div>
</template>
