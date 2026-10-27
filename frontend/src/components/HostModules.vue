<script setup>
// The integrations that work per host, as seen from one host.
import { onMounted, reactive, ref } from 'vue'
import { api, ApiError, enc } from '../api'
import { ago, toast } from '../store'
import ActionButton from './ActionButton.vue'
import JobBadge from './JobBadge.vue'
import ModuleView from './ModuleView.vue'
import SettingsForm from './SettingsForm.vue'

const props = defineProps({ host: String })
const mods = ref(null)
const open = ref('')
const values = reactive({})
const errors = ref({})
const views = reactive({})
const secrets = ref(null)

async function load() {
  try {
    mods.value = await api.get(`/api/hosts/${enc(props.host)}/modules`)
    for (const m of mods.value) values[m.integration] = JSON.parse(JSON.stringify(m.settings))
  } catch { mods.value = [] }
}
onMounted(async () => {
  await load()
  secrets.value = await api.get('/api/vault/refs').then((r) => r.refs).catch(() => null) // vault fields you may point at
  const first = mods.value?.find((m) => m.enabled && m.integration_enabled && m.has_view)
  if (first && mods.value.length === 1) toggle(first)
})

async function loadView(m) {
  views[m.integration] = { loading: true }
  try {
    views[m.integration] = await api.get(`/api/hosts/${enc(props.host)}/modules/${enc(m.integration)}/view`)
  } catch (e) {
    views[m.integration] = { error: e.message }
  }
}

function toggle(m) {
  open.value = open.value === m.integration ? '' : m.integration
  if (open.value && m.has_view && m.enabled && m.integration_enabled && !views[m.integration]) loadView(m)
}

async function save(m, extra = {}) {
  errors.value = {}
  try {
    const d = await api.put(`/api/hosts/${enc(props.host)}/modules/${enc(m.integration)}`, { settings: values[m.integration], ...extra })
    Object.assign(m, d)
    values[m.integration] = JSON.parse(JSON.stringify(d.settings))
    toast(`Saved ${m.integration} for ${props.host}`)
    if (m.has_view && m.enabled) loadView(m)
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
  }
}
</script>

<template>
  <section v-if="mods?.length" class="mods">
    <h2>Plugins for this host</h2>
    <div v-for="m in mods" :key="m.integration" class="card mod" :class="{ off: !m.enabled || !m.integration_enabled }">
      <div v-rowlink class="row head" role="button" @click="toggle(m)">
        <span class="caret">{{ open === m.integration ? '▾' : '▸' }}</span>
        <b class="mono">{{ m.integration }}</b>
        <span class="muted small">{{ m.plugin_name }}</span>
        <span v-if="!m.integration_enabled" class="status s-offline">integration disabled</span>
        <span v-else-if="m.enabled" class="status s-active">on</span>
        <span v-else class="status s-offline">off</span>
        <span class="small faint">{{ m.why }}</span>
        <span class="grow"></span>
        <span v-if="m.jobs[0]" class="small faint"><JobBadge :status="m.jobs[0].status" /> {{ m.jobs[0].action }} · {{ ago(m.jobs[0].created_at) }}</span>
      </div>

      <div v-if="open === m.integration" class="body">
        <div class="row" style="margin-bottom: 12px">
          <span class="small muted">For this host:</span>
          <div class="seg" role="radiogroup" :aria-label="`${m.integration} for this host`">
            <button v-for="o in [[null, `Follow “${m.selectors.join(' ') || 'none'}”`], [true, 'Always on'], [false, 'Off']]" :key="String(o[0])" type="button" role="radio"
                    :aria-checked="m.override === o[0]" :class="{ on: m.override === o[0] }" :disabled="!m.can_edit" @click="save(m, { enabled: o[0] })">{{ o[1] }}</button>
          </div>
        </div>

        <div class="split inner">
          <div>
            <div v-if="m.has_view && m.enabled && m.integration_enabled">
              <div class="row" style="margin-bottom: 8px"><span class="grow"></span>
                <button class="btn small ghost" @click="loadView(m)">Refresh</button></div>
              <p v-if="views[m.integration]?.loading" class="faint">Asking {{ m.integration }}…</p>
              <ModuleView v-else :view="views[m.integration]" />
            </div>
            <div v-if="m.actions.length && m.enabled && m.integration_enabled" class="acts">
              <h3>Actions</h3>
              <ActionButton v-for="a in m.actions" :key="a.id" :action="a" :disabled="!m.can_apply"
                            :url="`/api/hosts/${enc(host)}/modules/${enc(m.integration)}/actions/${a.id}`" />
              <p v-if="!m.can_apply" class="hint">Needs integration:apply and host:apply.</p>
            </div>
            <p v-if="!m.has_view && !m.actions.length" class="faint small">This plugin only has settings per host.</p>
          </div>
          <form v-if="m.host_settings.length" @submit.prevent="save(m)">
            <h3>Settings for this host</h3>
            <SettingsForm :fields="m.host_settings" v-modelv-model:values="values[m.integration]" :errors="errors" :secrets-set="m.secrets_set"
                          :secret-refs="m.secret_refs" :secrets="secrets" :disabled="!m.can_edit" />
            <button v-if="m.can_edit" class="btn primary small" style="margin-top: 12px">Save</button>
          </form>
        </div>
      </div>
    </div>
  </section>
  <p v-else-if="mods" class="emptyp">No plugin works per host here yet. Plugins like OPNsense, Technitium or Uptime Kuma show up on this tab once they're set up on the <RouterLink to="/plugins">Plugins</RouterLink> page and apply to this host.</p>
</template>

<style scoped>
.mods { margin-top: 24px; }
.mods > h2 { margin-bottom: 12px; }
.mod { padding: 0; }
.mod + .mod { margin-top: 10px; }
.mod.off .head b { color: var(--muted); }
.head { padding: 12px 16px; cursor: pointer; }
.caret { color: var(--faint); width: 12px; }
.body { padding: 0 16px 16px; border-top: 1px solid var(--line); padding-top: 14px; }
.inner { grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr); }
.acts { margin-top: 14px; display: grid; gap: 8px; }
</style>
