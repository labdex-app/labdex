<script setup>
import { confirm } from '../confirm'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError, enc } from '../api'
import Copy from '../components/Copy.vue'
import JobBadge from '../components/JobBadge.vue'
import ActionButton from '../components/ActionButton.vue'
import ModuleView from '../components/ModuleView.vue'
import SettingsForm from '../components/SettingsForm.vue'
import TemplateList from '../components/TemplateList.vue'
import { ago, toast } from '../store'

const props = defineProps({ name: String })
const route = useRoute()
const router = useRouter()
const d = ref(null)
const values = ref({})
const errors = ref({})
const networks = ref([])
const busy = ref('')
const notFound = ref(false)
const preview = ref(null) // { title, body }
const secrets = ref(null)
const selectors = ref('')

async function load() {
  try {
    d.value = await api.get(`/api/integrations/${enc(props.name)}`)
    values.value = JSON.parse(JSON.stringify(d.value.settings))
    selectors.value = (d.value.selectors || []).join(' ')
  } catch (e) {
    if (e.status === 404) notFound.value = true
    else toast(e.message, 'error')
  }
}
onMounted(async () => {
  await load()
  networks.value = await api.get('/api/networks').catch(() => [])
  secrets.value = await api.get('/api/vault/refs').then((r) => r.refs).catch(() => null) // vault fields you may point at
})

const spec = computed(() => d.value?.plugin)
const fields = computed(() => (spec.value?.settings || []).filter((f) => !f.key.startsWith('schedule_')))
const schedules = computed(() => (spec.value?.settings || []).filter((f) => f.key.startsWith('schedule_')))
const runActions = computed(() => (spec.value?.actions || []).filter((a) => a.mode !== 'export' && a.scope !== 'host'))
const hostActions = computed(() => (spec.value?.actions || []).filter((a) => a.scope === 'host'))
const exportActions = computed(() => (spec.value?.actions || []).filter((a) => a.mode === 'export'))
const dirty = computed(() => d.value && (JSON.stringify(values.value) !== JSON.stringify(d.value.settings) || selectors.value.trim() !== (d.value.selectors || []).join(' ')))
// pages a plugin adds (UniFi devices, Traefik routers …) are tabs here, not sidebar entries
const pages = computed(() => (d.value?.enabled ? (spec.value?.pages || []).filter((p) => p.place !== 'policy') : []))
// a page that reports on the zone policy lives on the Policy page (old links to the tab go there)
const policyPages = computed(() => (spec.value?.pages || []).filter((p) => p.place === 'policy'))
watch(policyPages, (ps) => { if (ps.some((p) => p.id === route.query.tab)) router.replace('/policy?tab=firewalls') })
const tab = computed(() => (pages.value.some((p) => p.id === route.query.tab) ? route.query.tab : ''))
const pageView = ref(null)
const pageBusy = ref(false)
async function loadPage() {
  if (!tab.value) return
  pageBusy.value = true
  try { pageView.value = await api.get(`/api/integrations/${enc(props.name)}/pages/${enc(tab.value)}`) } catch (e) { pageView.value = { error: e.message } }
  pageBusy.value = false
}
watch(tab, () => { pageView.value = null; loadPage() }, { immediate: true })
const setTab = (t) => router.replace({ query: t ? { tab: t } : {} })
const base = computed(() => `${location.origin}/api/integrations/${enc(props.name)}`)

async function save(extra = {}) {
  busy.value = 'save'
  errors.value = {}
  try {
    const body = { settings: values.value, ...extra }
    if (d.value.per_host) body.selectors = selectors.value
    d.value = await api.put(`/api/integrations/${enc(props.name)}`, body)
    values.value = JSON.parse(JSON.stringify(d.value.settings))
    selectors.value = (d.value.selectors || []).join(' ')
    toast(extra.enabled === undefined ? 'Settings saved' : extra.enabled ? 'Enabled' : 'Disabled')
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
  } finally {
    busy.value = ''
  }
}

async function show(title, url, json = false) {
  try {
    const body = json ? JSON.stringify(await api.get(url), null, 2) : await api.text(url)
    preview.value = { title, body, url }
  } catch (e) { toast(e.message, 'error') }
}

async function remove() {
  if (!(await confirm({ title: `Delete the integration “${props.name}”?`, body: `Its settings and job history go with it.`, action: 'Delete', danger: true }))) return
  await api.del(`/api/integrations/${enc(props.name)}`)
  toast(`Deleted ${props.name}`)
  router.push(`/plugins/${d.value.plugin_id}`)
}

const minutes = (m) => (!m ? 'off' : m % 1440 === 0 ? `every ${m / 1440} d` : m % 60 === 0 ? `every ${m / 60} h` : `every ${m} min`)
</script>

<template>
  <div class="page">
    <div v-if="notFound" class="errbox">No plugin instance called “{{ name }}”. <RouterLink to="/plugins">Back</RouterLink></div>
    <template v-else-if="d">
      <div class="page-head">
        <RouterLink :to="`/plugins/${d.plugin_id}`" class="btn ghost small">← {{ d.plugin_name }}</RouterLink>
        <h1 class="mono">{{ d.name }}</h1>
        <span class="sub">{{ d.plugin_name }}</span>
        <span v-if="d.enabled" class="status s-active">enabled</span><span v-else class="status s-offline">disabled</span>
        <span class="grow"></span>
        <template v-if="d.can_admin">
          <button class="btn danger" @click="remove">Delete</button>
          <button class="btn" :disabled="!!busy" @click="save({ enabled: !d.enabled })">{{ d.enabled ? 'Disable' : 'Enable' }}</button>
        </template>
      </div>
      <div v-if="d.error" class="errbox" style="margin-bottom: 16px">The plugin failed to load: <code>{{ d.error }}</code></div>
      <p v-if="spec?.description" class="muted" style="margin-top: -6px">{{ spec.description }}</p>
      <div v-if="d.plugin_id === 'backup' && !d.secrets_set.includes('passphrase')" class="warnbox" style="margin-bottom: 16px">
        Backups are <b>not encrypted</b>: set a passphrase below. Secrets inside stay encrypted either way (with secret.key, which is never in a backup).
      </div>

      <nav v-if="pages.length" class="tabs">
        <a href="#" :class="{ 'router-link-active': !tab }" :aria-current="!tab ? 'page' : undefined" @click.prevent="setTab('')">Overview</a>
        <a v-for="p in pages" :key="p.id" href="#" :class="{ 'router-link-active': tab === p.id }" :aria-current="tab === p.id ? 'page' : undefined"
           @click.prevent="setTab(p.id)">{{ p.label || p.id }}</a>
      </nav>
      <div v-if="tab" class="card pagecard">
        <button class="btn small refresh" :disabled="pageBusy" @click="loadPage">{{ pageBusy ? 'Loading…' : 'Refresh' }}</button>
        <ModuleView :view="pageView" />
      </div>
      <div v-else class="split">
        <div>
          <div v-if="d.pending_plans.length" class="card" style="border-color: var(--accent)">
            <h2>Plans waiting for review</h2>
            <ul class="plain">
              <li v-for="j in d.pending_plans" :key="j.id">
                <RouterLink :to="`/jobs/${j.id}`">#{{ j.id }} {{ j.action }}: {{ j.changes }} change{{ j.changes === 1 ? '' : 's' }}</RouterLink>
                <span class="small faint"> · {{ j.trigger }} · {{ ago(j.created_at) }}</span>
              </li>
            </ul>
          </div>

          <div v-if="runActions.length" class="card">
            <h2>Actions</h2>
            <div class="actions">
              <ActionButton v-for="a in runActions" :key="a.id" :action="a" :disabled="!d.enabled || !d.can_apply"
                            :url="`/api/integrations/${enc(d.name)}/actions/${a.id}`" />
            </div>
            <p v-if="!d.enabled" class="hint">Enable the integration to run actions.</p>
            <p v-else-if="!d.can_apply" class="hint">You can look, but running actions needs integration:apply.</p>
            <p v-if="policyPages.length && d.enabled" class="hint">How this firewall matches the zone policy: <RouterLink to="/policy?tab=firewalls">Policy › On the firewalls</RouterLink>.</p>
          </div>

          <TemplateList v-if="runActions.length || hostActions.length" :integration="d" />

          <div v-if="exportActions.length" class="card">
            <h2>Files</h2>
            <ul class="plain">
              <li v-for="a in exportActions" :key="a.id" class="row">
                <span style="flex: 1">{{ a.label || a.id }}</span>
                <button class="btn small ghost" :disabled="!d.enabled || !d.can_apply" @click="show(a.label || a.id, `${base}/export/${a.id}`)">View</button>
                <a class="btn small" :class="{ disabled: !d.enabled || !d.can_apply }" :href="`${base}/export/${a.id}?download=1`">Download</a>
              </li>
            </ul>
            <p class="hint">Exports cover the whole inventory, so they need integration:apply.</p>
          </div>

          <div v-if="spec?.endpoints?.length" class="card">
            <h2>Endpoints</h2>
            <ul class="plain">
              <li v-for="e in spec.endpoints" :key="e.id" style="margin-bottom: 8px">
                <div class="row"><span style="flex: 1">{{ e.label || e.id }}</span>
                  <button class="btn small ghost" :disabled="!d.enabled" @click="show(e.label || e.id, `${base}/endpoints/${e.id}`, true)">View</button></div>
                <Copy :text="`${base}/endpoints/${e.id}`" />
              </li>
            </ul>
            <p class="hint">Call these with a read-only API token: <code>Authorization: Bearer ldx_…</code>.</p>
          </div>

          <div v-if="preview" class="card">
            <div class="row" style="margin-bottom: 8px"><h2 style="margin: 0; flex: 1">{{ preview.title }}</h2>
              <button class="btn small ghost" @click="preview = null">Close</button></div>
            <pre class="code">{{ preview.body }}</pre>
          </div>

          <div class="card flush">
            <div style="padding: 14px 18px 4px"><h2>Recent jobs</h2></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>#</th><th>Action</th><th>Status</th><th>Trigger</th><th>When</th><th>Changes</th></tr></thead>
                <tbody>
                  <tr v-for="j in d.jobs" :key="j.id" class="click" v-rowlink @click="router.push(`/jobs/${j.id}`)">
                    <td class="faint">{{ j.id }}</td><td>{{ j.action }}</td><td><JobBadge :status="j.status" /></td>
                    <td class="small">{{ j.trigger }}<span v-if="j.trigger === 'manual'" class="faint"> · {{ j.created_by }}</span></td>
                    <td class="small">{{ ago(j.created_at) }}</td>
                    <td class="small">{{ j.mode === 'plan' ? j.changes : '' }}</td>
                  </tr>
                  <tr v-if="!d.jobs.length"><td colspan="6" class="empty">Nothing has run yet.</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <aside>
          <form v-if="fields.length || schedules.length" class="card" @submit.prevent="save()">
            <h2>Settings</h2>
            <fieldset class="bare stack" :disabled="!d.can_admin">
              <div v-if="d.per_host">
                <span class="flabel">Applies to</span>
                <input v-model="selectors" type="text" class="mono" placeholder="* or zone:DC_TC tag:admin name:srv-*" :class="{ invalid: errors.selectors }">
                <div v-if="errors.selectors" class="err">{{ errors.selectors }}</div>
                <div v-else class="hint">Hosts matching any of these. Each host can override it on its own page. Empty = only hosts switched on there.</div>
              </div>
              <SettingsForm :fields="fields" v-model:values="values" :errors="errors" :secrets-set="d.secrets_set" :secret-refs="d.secret_refs"
                            :networks="networks" :secrets="secrets" />
              <div v-if="schedules.length">
                <h3 style="margin-top: 8px">Schedule</h3>
                <div v-for="f in schedules" :key="f.key" class="row" style="margin-bottom: 6px">
                  <span style="flex: 1" class="small">{{ f.label }}</span>
                  <select v-model.number="values[f.key]" class="auto">
                    <option v-for="m in [0, 15, 60, 360, 1440, 10080]" :key="m" :value="m">{{ minutes(m) }}</option>
                    <option v-if="![0, 15, 60, 360, 1440, 10080, null, undefined].includes(values[f.key])" :value="values[f.key]">{{ minutes(values[f.key]) }}</option>
                  </select>
                </div>
                <p class="hint">Scheduled plans wait for review like any other plan.</p>
              </div>
            </fieldset>
            <div v-if="d.can_admin" class="row" style="margin-top: 14px">
              <button class="btn primary" :disabled="!dirty || !!busy">{{ busy === 'save' ? 'Saving…' : 'Save settings' }}</button>
              <button v-if="dirty" type="button" class="btn ghost" @click="values = JSON.parse(JSON.stringify(d.settings)); selectors = d.selectors.join(' ')">Reset</button>
            </div>
            <p v-else class="hint">Changing settings needs integration:admin.</p>
          </form>

          <div v-if="spec?.hooks?.length" class="card">
            <h3>Runs automatically after</h3>
            <ul class="plain">
              <li v-for="(h, n) in spec.hooks" :key="n" class="small">
                <code>{{ (h.events || []).join(', ') }}</code> → {{ h.action }}
              </li>
            </ul>
            <p class="hint">Hook plans wait for review; only the newest plan per action stays open.</p>
          </div>
          <div v-if="d.per_host" class="card">
            <h3>Hosts ({{ d.hosts?.length || 0 }})</h3>
            <p class="small" style="margin-top: 0">
              <RouterLink v-for="(h, n) in (d.hosts || []).slice(0, 40)" :key="h" :to="`/hosts/${h}`" class="mono">{{ h }}{{ n < Math.min(d.hosts.length, 40) - 1 ? ', ' : '' }}</RouterLink>
              <span v-if="d.hosts?.length > 40" class="faint"> … and {{ d.hosts.length - 40 }} more</span>
              <span v-if="!d.hosts?.length" class="faint">No host selected.</span>
            </p>
            <template v-if="d.host_overrides.length">
              <h3>Per-host overrides</h3>
              <ul class="plain small">
                <li v-for="o in d.host_overrides" :key="o.host"><RouterLink :to="`/hosts/${o.host}`" class="mono">{{ o.host }}</RouterLink>
                  <span class="faint"> {{ o.enabled === true ? 'on' : o.enabled === false ? 'off' : '' }}{{ o.settings ? ' · own settings' : '' }}</span></li>
              </ul>
            </template>
            <p v-if="hostActions.length" class="hint">Per-host actions ({{ hostActions.map((a) => a.label).join(', ') }}) are on each host's page.</p>
          </div>
          <div class="card">
            <h3>Plugin</h3>
            <dl class="kv small">
              <dt>id</dt><dd class="mono">{{ d.plugin_id }}</dd>
              <dt>version</dt><dd>{{ spec?.version || '—' }}</dd>
              <dt>source</dt><dd>{{ spec?.builtin ? 'part of labdex' : spec?.bundled ? 'development checkout' : spec?.replaces_bundled ? 'update from the repository' : 'plugin folder' }}</dd>
              <dt>code</dt><dd>{{ [spec?.python && 'Python', spec?.actions?.some((a) => a.ansible) && 'Ansible'].filter(Boolean).join(' + ') || 'manifest only' }}</dd>
            </dl>
          </div>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.actions { display: grid; gap: 10px; }
.pagecard { position: relative; }
.pagecard .refresh { position: absolute; top: 16px; right: 18px; }
.flabel { display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 4px; }
a.btn.disabled { opacity: .5; pointer-events: none; }
.stack > * + * { margin-top: 12px; }

</style>
