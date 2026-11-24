<script setup>
// The host page's YAML tab: the host's entry as its site file lists it, where that file lives
// in git, and Edit YAML with a Preview diff before saving. Saving works like the form: checked,
// recorded in Activity, and written to the site's git source when it has one.
import { computed, onMounted, ref } from 'vue'
import CodeDiff from './CodeDiff.vue'
import { api, ApiError } from '../api'
import { setQueryData } from '../query'
import { changed, toast } from '../store'

const props = defineProps({ host: { type: Object, required: true }, canEdit: Boolean,
  formDirty: Boolean }) // unsaved changes in the form: editing the YAML would lose them
const emit = defineEmits(['saved'])
const id = computed(() => props.host.uid || props.host.name)

const src = ref(null) // {yaml, site, git}
const mode = ref('view') // view | edit | diff
const text = ref('')
const preview = ref(null) // {before, after}
const error = ref('')
const busy = ref(false)
const dirty = computed(() => mode.value !== 'view' && text.value !== src.value?.yaml)
const git = computed(() => src.value?.git || null)
const file = computed(() => git.value?.path || `${src.value?.site || 'site'}.yaml`)

async function load() { src.value = await api.get(`/api/hosts/${encodeURIComponent(id.value)}/yaml`) }
onMounted(() => load().catch((e) => toast(e.message, 'error')))

function edit() { text.value = src.value.yaml; error.value = ''; mode.value = 'edit' }
function fail(e) {
  if (e instanceof ApiError && e.errors.length) error.value = e.errors.map((x) => (x.field === 'yaml' ? x.message : `${x.field}: ${x.message}`)).join('; ')
  else toast(e.message, 'error')
}
async function showDiff() {
  error.value = ''
  try {
    preview.value = await api.put(`/api/hosts/${encodeURIComponent(id.value)}/yaml`, { yaml: text.value, dry_run: true })
    mode.value = 'diff'
  } catch (e) { fail(e) }
}
async function save() {
  busy.value = true
  error.value = ''
  try {
    const h = await api.put(`/api/hosts/${encodeURIComponent(id.value)}/yaml`, { yaml: text.value })
    toast(git.value ? `Saved ${h.name}; it goes to git ${git.value.mode === 'mr' ? 'as a merge request' : `on ${git.value.branch}`}` : `Saved ${h.name}`)
    setQueryData(`/api/hosts/${h.uid}`, h)
    await changed()
    emit('saved', h)
    await load()
    preview.value = null
    mode.value = 'view'
  } catch (e) { fail(e); if (mode.value === 'diff') mode.value = 'edit' } finally { busy.value = false }
}
</script>

<template>
  <section class="card flush">
    <div class="yh">
      <div class="grow small">
        <template v-if="git">Saves to git <b class="mono">{{ git.branch }}</b> · <span class="mono">{{ git.path }}</span></template>
        <template v-else>Saves to labdex · recorded in Activity</template>
        <div class="muted">{{ mode === 'view' ? 'This host’s entry in its site file.' : dirty ? 'Edited as YAML' : 'No changes yet' }}</div>
      </div>
      <template v-if="mode === 'view'">
        <button v-if="canEdit && src" class="btn" :disabled="formDirty" :title="formDirty ? 'Save or discard the changes in the form first' : ''" @click="edit">Edit YAML</button>
      </template>
      <template v-else>
        <button class="btn ghost" :disabled="busy" @click="mode = 'view'">Cancel</button>
        <button class="btn" :disabled="busy || !dirty" @click="mode === 'diff' ? (mode = 'edit') : showDiff()">{{ mode === 'diff' ? 'Back to YAML' : 'Preview diff' }}</button>
        <button class="btn primary" :disabled="busy || !dirty" @click="save">{{ busy ? 'Saving…' : git ? 'Save and commit' : 'Save' }}</button>
      </template>
    </div>
    <div class="yb">
      <p v-if="!src" class="muted m-0">Loading…</p>
      <CodeDiff v-else-if="mode === 'view'" :after="src.yaml" :file="file" />
      <template v-else-if="mode === 'edit'">
        <textarea v-model="text" class="mono yedit" spellcheck="false" aria-label="The host as YAML" rows="22"></textarea>
        <p class="hint">Left-out fields take their defaults, like a file from git. Cables are changed on the Network tab.</p>
      </template>
      <CodeDiff v-else :before="preview.before" :after="preview.after" :file="file" />
      <div v-if="error" class="errbox small mt-2">{{ error }}</div>
    </div>
  </section>
</template>

<style scoped>
.yh { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 12px 16px; border-bottom: 1px solid var(--line); }
.yh b { font-weight: 600; }
.yb { padding: 16px; }
.yedit { width: 100%; font-size: calc(12.5px * var(--text)); line-height: 1.55; min-height: 360px; white-space: pre; tab-size: 2; }
</style>
