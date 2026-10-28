<script setup>
// The vault's entries as a list (all of them, or one host's). Names only: values stay in the
// entry dialog, revealed one field at a time.
import { KeyRound, Plus } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { toast } from '../store'
import Loading from './Loading.vue'
import VaultEntry from './VaultEntry.vue'

const props = defineProps({ host: { type: String, default: '' }, search: { type: String, default: '' } })
const status = ref(null)
const entries = ref(null)
const open = ref(null) // { uid } or { uid: null, start }

async function load() {
  try {
    const qs = new URLSearchParams()
    if (props.host) qs.set('host', props.host)
    if (props.search) qs.set('q', props.search)
    const [s, r] = await Promise.all([api.get('/api/vault'), api.get(`/api/vault/entries?${qs}`)])
    status.value = s
    entries.value = r.entries
  } catch (x) { toast(x.message, 'error') }
}
watch(() => [props.host, props.search], load, { immediate: true })
defineExpose({ load, add })
function add() {
  open.value = { uid: null, start: props.host ? { host: props.host, path: `hosts/${props.host}/` } : {} }
}
const sealed = computed(() => !!status.value?.sealed)
const stamp = (s) => (s ? new Date(s).toLocaleDateString() : '')
</script>

<template>
  <div class="card">
    <div v-if="host" class="row" style="justify-content: space-between; margin-bottom: 8px">
      <span class="small muted">Secrets stored for this host. <RouterLink to="/vault">The whole vault</RouterLink></span>
      <button v-if="status?.can_add && status?.initialized" class="btn small" :disabled="sealed" @click="add"><Plus :size="14" /> Secret for this host</button>
    </div>
    <p v-if="host && status && !status.initialized" class="faint" style="margin: 0">The vault isn’t set up yet: an administrator does that on the <RouterLink to="/vault">Vault</RouterLink> page.</p>
    <p v-else-if="host && sealed" class="hint">The vault is sealed: names show, values can’t be revealed or added until an administrator unseals it.</p>
    <Loading v-if="entries === null" />
    <p v-else-if="!entries.length && status?.initialized" class="faint" style="margin: 0">{{ search ? 'Nothing matches.' : host ? 'No secrets for this host yet.' : 'No secrets yet.' }}</p>
    <table v-else-if="entries.length">
      <thead><tr><th>Path</th><th class="hide-m">Fields</th><th v-if="!host" class="hide-m">Host</th><th class="hide-m">Updated</th></tr></thead>
      <tbody>
        <tr v-for="e in entries" :key="e.uid" class="click" tabindex="0" @click="open = { uid: e.uid }" @keydown.enter="open = { uid: e.uid }">
          <td><KeyRound :size="13" class="faint" /> <span class="mono">{{ e.path }}</span>
            <div v-if="e.description" class="small muted ell">{{ e.description }}</div></td>
          <td class="hide-m mono small">{{ e.fields.join(', ') }}</td>
          <td v-if="!host" class="hide-m mono small">{{ e.host }}</td>
          <td class="hide-m small muted">v{{ e.version }} · {{ stamp(e.updated_at) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <VaultEntry v-if="open" :uid="open.uid" :start="open.start" :sealed="sealed" :reauth="!!status?.reauth_needed" @close="open = null; load()" @changed="load" />
</template>

<style scoped>
.click { cursor: pointer; }
</style>
