<script setup>
// The vault page: set it up once, unseal it after every restart (administrators), and the list
// of secrets. Values are revealed one field at a time inside an entry, each reveal written to the history.
import { Lock, LockOpen, Plus } from 'lucide-vue-next'
import { ref } from 'vue'
import { api } from '../api'
import { confirm } from '../confirm'
import Field from '../components/Field.vue'
import Modal from '../components/Modal.vue'
import SecretList from '../components/SecretList.vue'
import { toast } from '../store'

const st = ref(null)
const list = ref(null)
const search = ref('')
const pass = ref({ one: '', two: '', error: '' })
const busy = ref(false)
const change = ref(null) // { current, next, again, errors }

async function load() {
  try { st.value = await api.get('/api/vault') } catch (x) { toast(x.message, 'error') }
}
load()

async function setUp() {
  const p = pass.value
  if (p.one !== p.two) { p.error = 'The two don’t match'; return }
  busy.value = true
  try {
    await api.post('/api/vault/init', { passphrase: p.one })
    pass.value = { one: '', two: '', error: '' }
    toast('The vault is set up and unsealed')
    await load()
    list.value?.load()
  } catch (x) { p.error = x.fields?.passphrase || x.message } finally { busy.value = false }
}
async function unseal() {
  busy.value = true
  try {
    await api.post('/api/vault/unseal', { passphrase: pass.value.one })
    pass.value = { one: '', two: '', error: '' }
    toast('Unsealed')
    await load()
    list.value?.load()
  } catch (x) { pass.value.error = x.fields?.passphrase || x.message } finally { busy.value = false }
}
async function seal() {
  if (!(await confirm({ title: 'Seal the vault?', body: 'Nobody can reveal or change values until an administrator unseals it again with the passphrase.', action: 'Seal' }))) return
  await api.post('/api/vault/seal', {})
  toast('Sealed')
  await load()
  list.value?.load()
}
async function savePassphrase() {
  const c = change.value
  c.errors = {}
  if (c.next !== c.again) { c.errors.again = 'The two don’t match'; return }
  busy.value = true
  try {
    await api.post('/api/vault/passphrase', { current: c.current, new: c.next })
    change.value = null
    toast('Passphrase changed')
  } catch (x) {
    c.errors = x.fields?.passphrase && /not the passphrase|too many/.test(x.message) ? { current: x.message } : { next: x.fields?.passphrase || x.message }
  } finally { busy.value = false }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Vault</h1>
      <span v-if="st?.initialized" class="sub" :class="st.sealed ? 'warn' : ''">
        <component :is="st.sealed ? Lock : LockOpen" :size="13" /> {{ st.sealed ? 'sealed' : 'unsealed' }}</span>
      <span class="grow"></span>
      <input v-if="st?.initialized" v-model.trim="search" type="search" placeholder="Path, host or description" style="max-width: 260px" aria-label="Search secrets">
      <template v-if="st?.initialized && !st.sealed">
        <button v-if="st.can_unseal" class="btn" @click="change = { current: '', next: '', again: '', errors: {} }">Change passphrase</button>
        <button v-if="st.can_unseal" class="btn" @click="seal"><Lock :size="14" /> Seal</button>
        <button v-if="st.can_add" class="btn primary" @click="list?.add()"><Plus :size="15" /> Secret</button>
      </template>
    </div>

    <p v-if="st?.legacy" class="warnbox small">{{ st.legacy }} global secret{{ st.legacy === 1 ? '' : 's' }} from before labdex 1.18
      {{ st.legacy === 1 ? 'moves' : 'move' }} into the vault, under <span class="mono">global/</span>, when an administrator {{ st.initialized ? 'unseals it' : 'sets it up' }}.
      Until then {{ st.legacy === 1 ? 'it works' : 'they work' }} as before; whatever points at {{ st.legacy === 1 ? 'it' : 'them' }} keeps working afterwards.</p>
    <template v-if="st && !st.initialized">
      <div v-if="st.can_unseal" class="card setup">
        <h2>Set up the vault</h2>
        <p>Root passwords, IPMI logins, Wi-Fi keys and recovery codes, kept in labdex and encrypted with a passphrase only you know.
          After every restart the vault is sealed until an administrator types it again.</p>
        <p class="warnbox small"><b>There’s no way back from a lost passphrase.</b> Nobody, not labdex and not a backup, can open the secrets without it. Write it down somewhere safe.</p>
        <form class="g2" @submit.prevent="setUp">
          <Field label="Passphrase" hint="At least 12 characters; a few words are easy to remember">
            <input v-model="pass.one" type="password" autocomplete="new-password"></Field>
          <Field label="Once more" :error="pass.error"><input v-model="pass.two" type="password" autocomplete="new-password"></Field>
          <div><button class="btn primary" :disabled="busy || !pass.one">Set up the vault</button></div>
        </form>
      </div>
      <p v-else class="empty">The vault isn’t set up yet. An administrator does that here.</p>
    </template>

    <template v-else-if="st">
      <div v-if="st.sealed" class="card setup">
        <h2><Lock :size="16" /> Sealed</h2>
        <template v-if="st.can_unseal">
          <p class="small muted">labdex restarted (or someone sealed it). Type the passphrase to open the vault again; until then names show, values don’t.</p>
          <form class="row" style="gap: 8px; align-items: flex-start" @submit.prevent="unseal">
            <Field label="Passphrase" :error="pass.error" style="flex: 1; min-width: 220px">
              <input v-model="pass.one" type="password" autocomplete="current-password" aria-label="Vault passphrase"></Field>
            <button class="btn primary" style="margin-top: 20px" :disabled="busy || !pass.one">Unseal</button>
          </form>
        </template>
        <p v-else class="small muted" style="margin: 0">Names show, values don’t, until an administrator unseals the vault with its passphrase.</p>
      </div>
      <SecretList ref="list" :search="search" />
    </template>

    <Modal v-if="change" title="Change the passphrase" :busy="busy" @close="change = null">
      <p class="small muted" style="margin: 0">The secrets stay as they are; only the passphrase that opens them changes.</p>
      <Field label="Current passphrase" :error="change.errors.current"><input v-model="change.current" type="password" autocomplete="current-password"></Field>
      <Field label="New passphrase" :error="change.errors.next" hint="At least 12 characters"><input v-model="change.next" type="password" autocomplete="new-password"></Field>
      <Field label="Once more" :error="change.errors.again"><input v-model="change.again" type="password" autocomplete="new-password"></Field>
      <template #foot>
        <button class="btn" @click="change = null">Cancel</button>
        <button class="btn primary" :disabled="busy || !change.current || !change.next" @click="savePassphrase">Change</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.setup { margin-bottom: 16px; }
.setup h2 { margin: 0 0 8px; font-size: 16px; display: flex; align-items: center; gap: 6px; }
.setup p { max-width: 70ch; }
.sub.warn { color: var(--warn); }
</style>
