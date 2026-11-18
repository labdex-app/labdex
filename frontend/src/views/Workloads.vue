<script setup>
// Workloads: every Compose stack the compose integrations keep, in a list, the picked one beside
// it with its compose file (edit, then a diff before saving) and its services. Saving changes
// labdex only; Plan deploy makes a plan that waits on Changes for an approval like any other.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Boxes, Play } from 'lucide-vue-next'
import { useQuery } from '../query'
import { api, ApiError } from '../api'
import { ago, changed, toast } from '../store'
import CodeDiff from '../components/CodeDiff.vue'
import JobBadge from '../components/JobBadge.vue'

const route = useRoute()
const router = useRouter()
const { data } = useQuery('/api/workloads', { onError: (e) => toast(e.message, 'error') })
const stacks = computed(() => data.value?.stacks || [])
const idOf = (s) => `${s.integration}/${s.host}/${s.name}`
const selected = computed(() => stacks.value.find((s) => idOf(s) === route.query.item) || stacks.value[0] || null)
function pick(s) { router.replace({ query: { ...route.query, item: idOf(s) } }) }
function back() { const { item: _, ...rest } = route.query; router.replace({ query: rest }) }

const enc = encodeURIComponent
const url = (s) => `/api/workloads/${enc(s.integration)}/${enc(s.host)}/${enc(s.name)}`
const detail = ref(null)
const text = ref('')
const view = ref('edit') // edit | diff
const preview = ref(null)
const error = ref('')
const busy = ref(false)
const dirty = computed(() => detail.value && text.value !== detail.value.compose)
async function load(s) {
  detail.value = null; preview.value = null; view.value = 'edit'; error.value = ''
  if (!s) return
  const d = await api.get(url(s))
  if (selected.value && idOf(selected.value) === idOf(s)) { detail.value = d; text.value = d.compose }
}
watch(() => selected.value && idOf(selected.value), () => load(selected.value).catch((e) => toast(e.message, 'error')), { immediate: true })

function fail(e) { error.value = e instanceof ApiError && e.errors.length ? e.errors.map((x) => x.message).join('; ') : e.message }
async function showDiff() {
  error.value = ''
  try {
    preview.value = await api.put(url(selected.value), { compose: text.value, dry_run: true })
    view.value = 'diff'
  } catch (e) { fail(e) }
}
async function save() {
  busy.value = true
  error.value = ''
  try {
    const d = await api.put(url(selected.value), { compose: text.value })
    detail.value = d; text.value = d.compose; view.value = 'edit'; preview.value = null
    await changed()
    toast(`Saved ${d.name}; Plan deploy puts it on ${d.host}`)
  } catch (e) { fail(e) } finally { busy.value = false }
}
const planning = ref(false)
async function planDeploy() {
  planning.value = true
  try {
    const s = selected.value
    const job = await api.post(`/api/integrations/${enc(s.integration)}/actions/deploy`, { params: { stacks: [s.name] } })
    router.push(`/changes?job=${job.id}`)
  } catch (e) { toast(e.message, 'error') } finally { planning.value = false }
}
const lines = computed(() => (text.value.match(/\n/g) || []).length + 1)
</script>

<template>
  <div class="page ld" :class="{ picked: !!route.query.item }">
    <section class="card flush list-pane" aria-label="Stacks">
      <div class="lp-head"><h1>Workloads</h1><span class="grow"></span>
        <span v-if="data" class="small faint mono">{{ stacks.length }} stack{{ stacks.length === 1 ? '' : 's' }}</span></div>
      <div v-if="!data" class="lp-empty faint">Loading…</div>
      <div v-else-if="!stacks.length" class="lp-empty">
        <Boxes :size="22" class="faint" />
        <p class="m-0"><b>No Compose stacks yet</b></p>
        <p class="small muted m-0">Install the Compose stacks plugin and add stacks to an integration; they show here.</p>
        <RouterLink to="/plugins" class="btn small mt-2">Plugins</RouterLink>
      </div>
      <ul v-else class="rows">
        <li v-for="s in stacks" :key="idOf(s)">
          <button class="row-btn" :class="{ on: selected === s }" :aria-current="selected === s ? 'true' : undefined" @click="pick(s)">
            <span class="dot" :class="s.last_deploy?.status === 'applied' ? 'ok' : s.last_deploy?.status === 'failed' ? 'fail' : 'unknown'" aria-hidden="true"></span>
            <span class="rb-main"><span class="rb-title mono">{{ s.name }}</span>
              <span class="small muted">on {{ s.host }} · {{ s.services.length }} service{{ s.services.length === 1 ? '' : 's' }}</span></span>
            <span class="rb-side"><span>{{ s.integration }}</span><span v-if="s.site">{{ s.site }}</span></span>
          </button>
        </li>
      </ul>
      <p v-for="p in data?.problems || []" :key="p.integration" class="small err pad-s">{{ p.integration }}: {{ p.errors.map((e) => `${e.field} ${e.message}`).join('; ') }}</p>
    </section>

    <section class="detail-pane wl" aria-label="The selected stack">
      <button class="btn small ld-back" @click="back">‹ Workloads</button>
      <template v-if="selected">
        <div class="wl-head">
          <span class="oh-icon" aria-hidden="true"><Boxes :size="22" /></span>
          <div class="grow">
            <div class="label">Compose stack · on <RouterLink :to="`/hosts/${selected.host}`" class="mono">{{ selected.host }}</RouterLink></div>
            <h2 class="mono wl-name">{{ selected.name }}
              <span v-if="selected.last_deploy" class="small faint wl-last">last deploy <JobBadge :status="selected.last_deploy.status" /> {{ ago(selected.last_deploy.at) }}</span></h2>
          </div>
          <span class="h-tag">integration <b>{{ selected.integration }}</b></span>
          <button v-if="selected.can_plan" class="btn primary" :disabled="planning || dirty" :title="dirty ? 'Save the compose file first' : 'Plans what changes on the host; it waits on Changes for approval'"
            @click="planDeploy"><Play :size="14" /> {{ planning ? 'Planning…' : 'Plan deploy' }}</button>
        </div>

        <div class="wl-grid">
          <section class="card flush">
            <div class="sect-head pad"><h3 class="mono">compose.yaml</h3>
              <span v-if="dirty" class="h-tag">unsaved change</span><span class="grow"></span>
              <div class="seg sm" role="group" aria-label="View">
                <button :class="{ on: view === 'edit' }" :aria-pressed="view === 'edit'" @click="view = 'edit'">{{ selected.can_edit ? 'Edit' : 'File' }}</button>
                <button :class="{ on: view === 'diff' }" :aria-pressed="view === 'diff'" :disabled="!dirty" @click="showDiff">Diff</button>
              </div></div>
            <p v-if="!detail" class="faint pad">Loading…</p>
            <template v-else-if="view === 'edit'">
              <div class="editor"><div class="gutter mono" aria-hidden="true"><span v-for="n in lines" :key="n">{{ n }}</span></div>
                <textarea v-model="text" class="mono" spellcheck="false" :readonly="!selected.can_edit" aria-label="The compose file" :rows="Math.max(lines, 14)"></textarea></div>
            </template>
            <div v-else class="pad"><CodeDiff :before="preview.before" :after="preview.after" file="compose.yaml" /></div>
            <div v-if="error" class="err pad">{{ error }}</div>
            <div v-if="selected.can_edit" class="foot pad"><span class="small muted grow">Saving changes labdex only; <b>Plan deploy</b> puts it on the host after an approval.</span>
              <button class="btn" :disabled="!dirty" @click="text = detail.compose; view = 'edit'">Discard</button>
              <button class="btn primary" :disabled="!dirty || busy" @click="save">{{ busy ? 'Saving…' : 'Save' }}</button></div>
          </section>

          <div class="side">
            <section class="card flush">
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Service</th><th>Image</th><th>Ports</th></tr></thead>
                  <tbody><tr v-for="v in selected.services" :key="v.name">
                    <td class="mono">{{ v.name }}</td><td class="mono small">{{ v.image || '—' }}</td><td class="mono small">{{ v.ports.join(', ') || '—' }}</td></tr></tbody>
                </table>
              </div>
            </section>
            <section v-if="selected.env.length" class="card">
              <h3 class="label">Environment</h3>
              <p class="small m-0"><span v-for="k in selected.env" :key="k" class="h-tag mono">{{ k }}</span></p>
              <p class="hint">Values and vault references are in the integration's settings.</p>
            </section>
            <p class="hint">Running state, logs and use come from the host: <b>Plan deploy</b> reads each stack folder before it plans.
              <RouterLink :to="`/integrations/${selected.integration}`">Integration</RouterLink></p>
          </div>
        </div>
      </template>
      <p v-else-if="data" class="empty">Nothing to look at here.</p>
    </section>
  </div>
</template>

<style scoped>
.wl { padding: 0 !important; background: none; border: 0; }
.wl-head { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; flex-wrap: wrap; }
.oh-icon { width: 44px; height: 44px; flex: none; display: grid; place-items: center; border: 1px solid var(--line); border-radius: var(--r-box); background: var(--card); color: var(--muted); }
.wl-name { margin: 2px 0 0; font-size: calc(20px * var(--text)); display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.wl-last { font-family: var(--sans, inherit); font-weight: 400; display: inline-flex; gap: 6px; align-items: center; }
.wl-grid { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 14px; align-items: start; }
.wl-grid .card { margin: 0; }
.side { display: flex; flex-direction: column; gap: 14px; }
.pad { padding: 12px 16px; margin: 0; }
.pad-s { padding: 8px 16px; margin: 0; }
.sect-head h3 { margin: 0; font-size: calc(13px * var(--text)); }
.editor { display: flex; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); background: var(--code-bg, var(--panel)); }
.gutter { display: flex; flex-direction: column; padding: 10px 8px; text-align: right; color: var(--faint); font-size: calc(12.5px * var(--text)); line-height: 1.6; user-select: none; min-width: 36px; }
.editor textarea { flex: 1; border: 0; border-radius: 0; background: transparent; resize: vertical; padding: 10px 12px; font-size: calc(12.5px * var(--text)); line-height: 1.6; white-space: pre; overflow-x: auto; tab-size: 2; }
.editor textarea:focus { outline: none; box-shadow: none; }
.foot { display: flex; gap: 8px; align-items: center; }
.h-tag { margin-right: 4px; }
@media (max-width: 1200px) { .wl-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
