<script setup>
// The last step of installing a downloaded or uploaded plugin: what it is, whose signature it
// carries, what it runs, and an explicit OK for anything unsigned. Shared by Settings › Plugins and the Marketplace.
import { ref } from 'vue'
import { api } from '../api'
import { toast } from '../store'
import TrustBadge from './TrustBadge.vue'

const props = defineProps({ staged: { type: Object, required: true }, isolation: { type: Object, default: null } })
const emit = defineEmits(['done', 'cancel'])
const accept = ref(false)
const busy = ref(false)
const isolates = (t) => props.isolation && props.isolation.mode !== 'off' && ['community', 'unsigned'].includes(t?.status)

async function install() {
  busy.value = true
  try {
    const d = await api.post(`/api/plugins/stage/${props.staged.token}/confirm`, { accept_unsigned: accept.value })
    toast(`Installed ${d.name} ${d.version}`)
    emit('done', d)
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div class="card stage" :class="`t-${staged.trust.status}`">
    <div class="row"><h2 class="m-0">Install {{ staged.name }} {{ staged.version }}?</h2><TrustBadge :trust="staged.trust" />
      <span class="grow"></span><button class="btn ghost small" @click="emit('cancel')">Cancel</button></div>
    <p class="muted">{{ staged.description }}</p>
    <dl class="kv small">
      <dt>id</dt><dd class="mono">{{ staged.id }}</dd>
      <dt>signature</dt><dd>{{ staged.trust.detail }}<span v-if="staged.trust.key_id" class="faint mono"> · key {{ staged.trust.key_id }}</span></dd>
      <dt>runs</dt><dd>{{ [staged.python && (isolates(staged.trust) ? 'Python code, isolated' : 'Python code inside labdex'), staged.ansible.length && `Ansible: ${staged.ansible.join(', ')}`].filter(Boolean).join('; ') || 'nothing (manifest only)' }}</dd>
      <dt>replaces</dt><dd>{{ staged.installed_version ? `installed version ${staged.installed_version}` : '—' }}</dd>
      <dt>files</dt><dd class="mono small">{{ staged.files.join(', ') }}</dd>
      <dt>from</dt><dd class="small">{{ staged.source }}</dd>
    </dl>
    <div v-if="!staged.installable" class="errbox mt-3">Refused: {{ staged.trust.detail }}. labdex never runs a plugin whose signature doesn't hold.</div>
    <template v-else>
      <div v-if="staged.needs_confirmation" class="warnbox mt-3">
        <template v-if="isolates(staged.trust) && isolation.level === 'full'">This plugin isn't signed. Its code runs isolated: it can read the inventory
          and its own settings, but not labdex's database, other integrations' secrets or other plugins.</template>
        <template v-else-if="isolates(staged.trust)">This plugin isn't signed. Its code runs in its own process, but this machine can't lock it out of
          labdex's files (no Landlock), so it could read the database and every integration's secrets.</template>
        <template v-else>This plugin isn't signed. Its code runs inside labdex with access to the whole inventory and the integration secrets.</template>
        <label class="check tick" style="margin-top: 8px; display: flex"><input v-model="accept" type="checkbox"> I trust its author and have looked at the code</label>
      </div>
      <button class="btn primary mt-3" :disabled="busy || (staged.needs_confirmation && !accept)" @click="install">Install</button>
    </template>
  </div>
</template>

<style scoped>
.stage { border-color: var(--accent); }
.stage.t-invalid { border-color: var(--err); }
.stage.t-unsigned { border-color: var(--warn); }
</style>
