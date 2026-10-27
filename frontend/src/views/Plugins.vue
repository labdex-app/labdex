<script setup>
// Installed plugins with their instances (integrations), and the Marketplace for the rest.
import { Plus } from 'lucide-vue-next'
import FilterSelect from '../components/FilterSelect.vue'
import Field from '../components/Field.vue'
import Modal from '../components/Modal.vue'
import Marketplace from '../components/Marketplace.vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import TrustBadge from '../components/TrustBadge.vue'
import { can, state, toast } from '../store'

const router = useRouter()
const route = useRoute()
const tab = computed(() => (route.query.tab === 'market' ? 'market' : 'installed'))
const setTab = (t) => router.replace({ query: { ...route.query, tab: t === 'installed' ? undefined : t } })
const available = ref(0)
const updates = ref(0)
const plugins = ref([])
const caps = ref({})
const loadErrors = ref([])
const q = ref('')
const show = ref('all')
const cap = ref('')
const adding = ref(null)
const newName = ref('')
const nameError = ref('')

async function load() {
  const p = await api.get('/api/plugins')
  plugins.value = p.plugins
  caps.value = p.capabilities
  loadErrors.value = p.errors
  available.value = p.available || 0
}
onMounted(() => load().catch((e) => toast(e.message, 'error')))

const isAdmin = computed(() => can('integration', 'admin'))
const shown = computed(() => {
  const n = q.value.trim().toLowerCase()
  return plugins.value.filter((p) => (show.value === 'all' || p.instances.length) && (!cap.value || p.capabilities.includes(cap.value))
    && (!n || [p.id, p.name, p.description, ...(p.tags || []), ...p.instances.map((i) => i.name)].join(' ').toLowerCase().includes(n)))
})
const usedCaps = computed(() => Object.keys(caps.value).filter((c) => plugins.value.some((p) => p.capabilities.includes(c))))

function startAdd(p) {
  adding.value = p
  newName.value = p.instances.length ? `${p.id}-${p.instances.length + 1}` : p.id
  nameError.value = ''
}
async function create() {
  try {
    const d = await api.post('/api/integrations', { plugin_id: adding.value.id, name: newName.value })
    router.push(`/integrations/${d.name}`)
  } catch (e) {
    nameError.value = e instanceof ApiError && e.errors.length ? e.errors.map((x) => x.message).join('; ') : e.message
  }
}
const dot = (i) => (!i.enabled ? 'off' : i.last_status === 'failed' ? 'bad' : 'ok')
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Plugins</h1>
      <span class="sub">What talks to your firewall, DNS, monitoring and hosts, and what makes files and pages. Each instance of a plugin has its own settings.</span>
      <span class="grow"></span>
      <RouterLink v-if="state.meta.is_admin" to="/admin/plugins" class="btn small ghost">Repositories and upload</RouterLink>
    </div>

    <nav class="tabs" aria-label="Plugins">
      <a href="#" :class="{ 'router-link-active': tab === 'installed' }" :aria-current="tab === 'installed' ? 'page' : undefined" @click.prevent="setTab('installed')">
        Installed <span class="note">{{ plugins.length }}</span></a>
      <a href="#" :class="{ 'router-link-active': tab === 'market' }" :aria-current="tab === 'market' ? 'page' : undefined" @click.prevent="setTab('market')">
        Marketplace <span v-if="updates" class="pill info" :title="`${updates} update${updates === 1 ? '' : 's'}`">{{ updates }}</span></a>
    </nav>

    <Marketplace v-if="tab === 'market'" @changed="load" @updates="(n) => (updates = n)" />
    <template v-else>
    <div class="filters">
      <FilterSelect v-model="show" label="Show" any="" :options="[{ value: 'all', label: 'all installed' }, { value: 'used', label: 'in use' }]" />
      <FilterSelect :model-value="cap" label="Brings" :options="usedCaps" @update:model-value="(v) => (cap = v || '')" />
      <span class="grow"></span>
      <input v-model="q" type="search" placeholder="Search plugins and instances" style="max-width: 260px" aria-label="Search plugins">
    </div>

    <div class="pgrid">
      <div v-for="p in shown" :key="p.id" class="card pcard">
        <div class="row" style="flex-wrap: nowrap">
          <RouterLink :to="`/plugins/${p.id}`" class="pname">{{ p.name }}</RouterLink>
          <span class="grow"></span>
          <TrustBadge :trust="p.trust" />
        </div>
        <p class="small muted desc">{{ p.description }}</p>
        <div class="caps"><span v-for="c in p.capabilities" :key="c" class="tag" :title="caps[c]">{{ c }}</span></div>
        <div v-if="p.error" class="errbox small mono">{{ p.error }}</div>
        <ul v-if="p.instances.length" class="plain insts">
          <li v-for="i in p.instances" :key="i.name">
            <span class="dot" :class="dot(i)" :title="!i.enabled ? 'disabled' : i.last_status || 'never ran'"></span>
            <RouterLink :to="`/integrations/${i.name}`" class="mono">{{ i.name }}</RouterLink>
            <span v-if="!i.enabled" class="faint small">disabled</span>
          </li>
        </ul>
        <template v-if="isAdmin && !p.error">
          <button class="btn small add" @click="startAdd(p)"><Plus :size="14" /> {{ p.instances.length ? 'Another instance' : 'Add an instance' }}</button>
        </template>
      </div>
      <p v-if="!shown.length" class="faint">No plugin matches.<template v-if="show === 'used'"> <a href="#" @click.prevent="show = 'all'">Show all plugins</a>.</template></p>
    </div>
    <p v-if="available" class="small muted more">{{ available }} more plugin{{ available === 1 ? '' : 's' }} ship{{ available === 1 ? 's' : '' }} with labdex but {{ available === 1 ? "isn't" : "aren't" }} installed.
      <a href="#" @click.prevent="setTab('market')">Browse the Marketplace</a></p>
    </template>
    <div v-if="loadErrors.length" class="errbox" style="margin-top: 16px"><div v-for="e in loadErrors" :key="e.path" class="small"><code>{{ e.path }}</code> {{ e.error }}</div></div>
    <Modal v-if="adding" :title="`New ${adding.name || adding.id} instance`" @close="adding = null">
      <form id="inst-form" @submit.prevent="create">
        <Field label="Instance name" :error="nameError" hint="Lowercase letters, digits and dashes. You set it up on the next page.">
          <input v-model.trim="newName" type="text" class="mono" aria-label="Instance name"></Field>
      </form>
      <template #foot>
        <button type="button" class="btn ghost" @click="adding = null">Cancel</button>
        <button form="inst-form" class="btn primary">Create instance</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.pgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 14px; }
.pcard { display: flex; flex-direction: column; gap: 6px; margin: 0; }
.pname { font-weight: 650; font-size: 15px; color: var(--ink); }
.desc { margin: 0; }
.caps { display: flex; flex-wrap: wrap; gap: 4px; }
.insts { margin: 4px 0 0; padding-top: 6px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 3px; }
.insts li { display: flex; align-items: center; gap: 8px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); flex: none; }
.dot.ok { background: var(--ok); } .dot.bad { background: var(--err); } .dot.off { opacity: .4; }
.add { align-self: flex-start; margin-top: auto; }
.more { margin: 18px 0 0; }
</style>
