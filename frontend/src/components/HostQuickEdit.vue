<script setup>
// Quick edit beside the Hosts list: the fields people change most (name, kind, site, zone, what
// it does, tags, features, notes). It saves the same way as the host page: checked, recorded in
// Activity, and written to the site's git file when the site has one. Preview shows that file's
// lines for this host before and after. Interfaces, services and the rest stay on the host page.
import { computed, onMounted, reactive, ref } from 'vue'
import { ArrowUpRight } from 'lucide-vue-next'
import Drawer from './Drawer.vue'
import Field from './Field.vue'
import ChipInput from './ChipInput.vue'
import CodeDiff from './CodeDiff.vue'
import { api, ApiError } from '../api'
import { getCached, setQueryData } from '../query'
import { changed, state, tagColour, toast } from '../store'

const props = defineProps({ host: { type: Object, required: true } })
const emit = defineEmits(['close', 'saved', 'view'])
const id = computed(() => props.host.uid || props.host.name)

const FIELDS = ['name', 'kind', 'site', 'zone', 'status', 'function', 'fun_names', 'tags', 'features', 'notes']
const form = reactive({ name: '', kind: 'device', site: '', zone: '', status: 'active', function: '', fun_names: [], tags: [], features: [], notes: '' })
const orig = ref(null)
const full = ref(null)
const src = ref(null) // {yaml, site, git}
const mode = ref('form')
const preview = ref(null) // {before, after}
const errors = ref({})
const busy = ref(false)
const featureList = ref([])
const tagList = ref([])

function snap(h) {
  return { name: h.name, kind: h.kind || 'device', site: h.site, zone: h.zone_pinned ? h.zone : '', status: h.status, function: h.function || '',
    fun_names: [...(h.fun_names || (h.fun ? [h.fun] : []))], tags: [...(h.tags || [])], features: [...(h.features || [])], notes: h.notes || '' }
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
const diff = computed(() => {
  if (!orig.value) return {}
  return Object.fromEntries(FIELDS.filter((k) => !same(form[k], orig.value[k])).map((k) => [k, form[k]]))
})
const dirty = computed(() => Object.keys(diff.value).length > 0)
const LABELS = { fun_names: 'fun name', function: 'function' }
const summary = computed(() => Object.keys(diff.value).map((k) => LABELS[k] || k).join(', '))
const err = (k) => errors.value[k] || ''
const zoneFromNet = computed(() => full.value && !full.value.zone_pinned ? full.value.zone : '')
const ifs = computed(() => (full.value?.interfaces || []).filter((i) => i.ip4 || i.ip6 || i.network))
const git = computed(() => src.value?.git || null)
const file = computed(() => git.value?.path || `${form.site || 'site'}.yaml`)

async function load() {
  const [h, y] = await Promise.all([api.get(`/api/hosts/${encodeURIComponent(id.value)}`), api.get(`/api/hosts/${encodeURIComponent(id.value)}/yaml`)])
  full.value = h
  src.value = y
  orig.value = snap(h)
  Object.assign(form, snap(h))
}
onMounted(() => {
  load().catch((e) => toast(e.message, 'error'))
  getCached('/api/features', 60000).then((f) => { featureList.value = f.map((x) => x.path) }).catch(() => {})
  getCached('/api/tags', 60000).then((t) => { tagList.value = t.map((x) => x.name) }).catch(() => {})
})

function fail(e) {
  if (e instanceof ApiError && e.errors.length) {
    errors.value = e.fields
    toast('Please fix the highlighted fields', 'error')
  } else toast(e.message, 'error')
}

async function showDiff() {
  errors.value = {}
  try {
    preview.value = await api.patch(`/api/hosts/${encodeURIComponent(id.value)}`, { ...diff.value, dry_run: true })
    mode.value = 'diff'
  } catch (e) { fail(e) }
}

async function showYaml() {
  mode.value = 'yaml'
  if (dirty.value) await showDiff().then(() => { mode.value = 'yaml' })
}

async function save() {
  busy.value = true
  errors.value = {}
  try {
    const h = await api.patch(`/api/hosts/${encodeURIComponent(id.value)}`, diff.value)
    toast(git.value ? `Saved ${h.name}; it goes to git ${git.value.mode === 'mr' ? 'as a merge request' : `on ${git.value.branch}`}` : `Saved ${h.name}`)
    setQueryData(`/api/hosts/${h.uid}`, h)
    await changed()
    emit('saved', h)
    await load()
    preview.value = null
    mode.value = 'form'
  } catch (e) { fail(e) } finally { busy.value = false }
}

function addFeature(e) {
  const v = e.target.value
  if (v && !form.features.includes(v)) form.features.push(v)
  e.target.value = ''
}
</script>

<template>
  <Drawer label="Edit host" :title="full?.name || host.name" @close="emit('close')">
    <template #head>
      <div class="seg sm" role="tablist" aria-label="View">
        <button role="tab" :aria-selected="mode === 'form'" :class="{ on: mode === 'form' }" @click="mode = 'form'">Form</button>
        <button role="tab" :aria-selected="mode !== 'form'" :class="{ on: mode !== 'form' }" @click="showYaml">YAML</button>
      </div>
    </template>

    <p v-if="!full" class="muted">Loading…</p>
    <template v-else-if="mode === 'form'">
      <fieldset :disabled="full.can_edit === false" class="bare">
        <h3 class="label sect first">Identity</h3>
        <div class="grid2">
          <Field label="Name" :error="err('name')"><input v-model.trim="form.name" class="mono" spellcheck="false" autocomplete="off"></Field>
          <Field label="Kind" :error="err('kind')">
            <select v-model="form.kind"><option value="device">Device</option><option value="vip">VIP (shared address)</option></select>
          </Field>
          <Field v-if="(state.meta.sites || []).length > 1" label="Site" :error="err('site')">
            <select v-model="form.site"><option v-for="s in state.meta.sites" :key="s.code" :value="s.code">{{ s.name || s.code }}</option></select>
          </Field>
          <Field label="Zone" :error="err('zone')">
            <select v-model="form.zone">
              <option value="">From its network{{ zoneFromNet ? ` (${zoneFromNet})` : '' }}</option>
              <option v-for="z in state.meta.zones" :key="z.code" :value="z.code">{{ z.code }} · {{ z.long }}</option>
            </select>
          </Field>
          <Field label="Status" :error="err('status')">
            <select v-model="form.status"><option v-for="s in state.meta.statuses" :key="s" :value="s">{{ s }}</option></select>
          </Field>
          <Field label="Fun name" :error="err('fun')">
            <ChipInput v-model="form.fun_names" mono label="Add a fun name" placeholder="snorlax" :normalize="(v) => v.trim().toLowerCase()" />
          </Field>
        </div>
        <Field label="Function" :error="err('function')" hint="What it does, in words"><input v-model="form.function"></Field>

        <h3 class="label sect">Interfaces</h3>
        <div v-if="ifs.length" class="ifbox">
          <table class="mini">
            <thead><tr><th>Port</th><th>Network</th><th>Address</th></tr></thead>
            <tbody>
              <tr v-for="(i, n) in ifs" :key="n">
                <td class="mono">{{ i.label || '—' }}</td><td>{{ i.network }}</td>
                <td class="mono">{{ i.ip4 }}<div v-if="i.ip6" class="faint small">{{ i.ip6 }}</div></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="hint">Interfaces, services and cables are edited on the
          <RouterLink :to="`/hosts/${full.uid}?tab=network`">host page <ArrowUpRight :size="12" /></RouterLink>.</p>

        <h3 class="label sect">Automation features</h3>
        <div class="row chips">
          <span v-for="(f, n) in form.features" :key="f" class="chip on mono">{{ f }}
            <button type="button" class="chipx" :aria-label="`Remove ${f}`" @click="form.features.splice(n, 1)">✕</button></span>
          <select class="small auto" aria-label="Add feature" @change="addFeature">
            <option value="">+ add feature</option>
            <option v-for="f in featureList.filter((x) => !form.features.includes(x))" :key="f" :value="f">{{ f }}</option>
          </select>
        </div>
        <div v-if="err('features')" class="err">{{ err('features') }}</div>

        <h3 class="label sect">Tags</h3>
        <ChipInput v-model="form.tags" :suggestions="tagList" :colour="tagColour" label="Add tag" placeholder="backup" />
        <div v-if="err('tags')" class="err">{{ err('tags') }}</div>

        <h3 class="label sect">Notes</h3>
        <textarea v-model="form.notes" rows="3" aria-label="Notes"></textarea>
      </fieldset>
    </template>
    <template v-else-if="mode === 'yaml'">
      <CodeDiff :after="preview && dirty ? preview.after : src.yaml" :file="file" />
      <p class="hint">This host's entry in the site file{{ dirty ? ', with your changes' : '' }}. Edit it in the form, or in git.</p>
    </template>
    <template v-else>
      <CodeDiff :before="preview.before" :after="preview.after" :file="file" />
      <p class="hint">{{ git ? `Saving commits this to ${git.mode === 'mr' ? 'a merge request into' : ''} ${git.branch}.` : 'This site has no git source; the change is saved in labdex and recorded in Activity.' }}</p>
    </template>

    <template #foot>
      <div class="where">
        <div class="small"><template v-if="git">Saves to git <b class="mono">{{ git.branch }}</b> · <span class="mono">{{ git.path }}</span></template>
          <template v-else>Saves to labdex · recorded in Activity</template></div>
        <div class="small muted">{{ dirty ? `${Object.keys(diff).length} change${Object.keys(diff).length === 1 ? '' : 's'}: ${summary}` : 'No changes yet' }}</div>
      </div>
      <button class="btn" :disabled="!dirty || busy" @click="mode === 'diff' ? (mode = 'form') : showDiff()">{{ mode === 'diff' ? 'Back to form' : 'Preview diff' }}</button>
      <button v-if="full?.can_edit !== false" class="btn primary" :disabled="!dirty || busy" @click="save">
        {{ busy ? 'Saving…' : git ? 'Save and commit' : 'Save' }}</button>
    </template>
  </Drawer>
</template>

<style scoped>
.sect { margin: 20px 0 8px; }
.sect.first { margin-top: 2px; }
.grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; margin-bottom: 10px; }
@media (max-width: 520px) { .grid2 { grid-template-columns: minmax(0, 1fr); } }
.ifbox { border: 1px solid var(--line); border-radius: var(--r-box); overflow: hidden; }
table.mini { width: 100%; }
table.mini th { padding: 6px 10px; }
table.mini td { padding: 7px 10px; }
.chips { gap: 6px; flex-wrap: wrap; }
textarea { width: 100%; }
.where { flex: 1; min-width: 0; }
.where b { font-weight: 600; }
</style>
