<script setup>
// SSH login keys through the host agents (sshkeys.py): people's public keys, where each may log
// in, every host's state against its agent's last report, break-glass keys, and apply.
// labdex's keys go into /etc/ssh/labdex on the host, never into ~/.ssh/authorized_keys.
import { computed, reactive, ref } from 'vue'
import { KeyRound, Plus, X } from 'lucide-vue-next'
import { api, enc } from '../api'
import { useQuery } from '../query'
import { changed, state, toast } from '../store'
import { confirm } from '../confirm'
import Modal from '../components/Modal.vue'
import Field from '../components/Field.vue'
import Copy from '../components/Copy.vue'

const query = useQuery(() => '/api/ssh-keys', { onError: (e) => toast(e.message, 'error') })
const keys = computed(() => query.data.value?.keys || [])
const hosts = computed(() => query.data.value?.hosts || [])
const admin = computed(() => Boolean(state.meta?.is_admin))

const STATES = {
  in_sync: ['In sync', 'ok'],
  drift: ['Differs', 'warn'],
  no_break_glass: ['No break-glass key', 'warn'],
  unmanaged_keys: ['Other keys too', 'warn'],
  report_only: ['Report only', ''],
  unknown: ['No report yet', ''],
  no_agent: ['No Go agent', ''],
}
const TILES = [
  ['in_sync', 'In sync', 'ok', 'labdex and the host agree'],
  ['drift', 'Differ', 'warn', 'an apply would change them'],
  ['no_break_glass', 'No break-glass', 'warn', 'no emergency key yet'],
  ['unmanaged_keys', 'Other keys', 'warn', 'keys labdex doesn\'t manage'],
]
const filter = ref('')
const counts = computed(() => Object.fromEntries(TILES.map(([k]) => [k, hosts.value.filter((h) => h.state === k).length])))
const rows = computed(() => hosts.value.filter((h) => !filter.value || h.state === filter.value))

// one host's plan, opened under its row
const open = ref('')
const detail = ref(null)
async function toggle(host) {
  if (open.value === host) { open.value = ''; return }
  open.value = host
  detail.value = null
  try { detail.value = await api.get(`/api/hosts/${enc(host)}/ssh-keys`) } catch (e) { toast(e.message, 'error') }
}

const busy = ref(false)
async function run(fn, done) {
  busy.value = true
  try { await fn(); if (done) toast(done); await changed(); query.refresh() } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
async function apply(h) {
  const ok = await confirm({ title: `Apply SSH keys to ${h.host}?`, action: 'Apply',
    body: `labdex writes the keys of ${h.ssh_users.join(', ')} on ${h.host}: ${h.add} to add, ${h.remove} to remove. ` +
      'Keys in the users\' own authorized_keys stay as they are.' })
  if (ok) await run(() => api.post(`/api/hosts/${enc(h.host)}/ssh-keys/apply`), `Apply queued for ${h.host}`)
}
const report = (h) => run(() => api.post(`/api/hosts/${enc(h.host)}/ssh-keys/report`), `Report asked from ${h.host}`)

// adding a key
const adding = ref(false)
const form = reactive({ name: '', owner: '', public_key: '', note: '' })
const formError = ref('')
function startAdd() { Object.assign(form, { name: '', owner: '', public_key: '', note: '' }); formError.value = ''; adding.value = true }
async function addKey() {
  formError.value = ''
  busy.value = true
  try { await api.post('/api/ssh-keys', { ...form }); adding.value = false; toast(`Added ${form.name}`); await changed(); query.refresh() }
  catch (e) { formError.value = e.message } finally { busy.value = false }
}
async function removeKey(k) {
  const where = k.grants.map((g) => `${g.user}@${g.host}`).join(', ')
  const ok = await confirm({ title: `Remove ${k.name}?`, danger: true, action: 'Remove',
    body: where ? `It stops being labdex's for ${where}. It leaves the hosts with their next apply.` : 'It isn\'t on any host.' })
  if (ok) await run(() => api.del(`/api/ssh-keys/${k.id}`), `Removed ${k.name}`)
}

// granting
const granting = ref(null)
const grant = reactive({ host: '*', user: '' })
const grantError = ref('')
const hostNames = computed(() => hosts.value.map((h) => h.host))
function startGrant(k) { granting.value = k; Object.assign(grant, { host: '*', user: '' }); grantError.value = '' }
async function addGrant() {
  grantError.value = ''
  busy.value = true
  try { await api.post(`/api/ssh-keys/${granting.value.id}/grants`, { ...grant }); granting.value = null; await changed(); query.refresh() }
  catch (e) { grantError.value = e.message } finally { busy.value = false }
}
const ungrant = (k, g) => run(() => api.del(`/api/ssh-keys/grants/${g.id}`), `${k.name} no longer for ${g.user}@${g.host}`)

// break-glass
const glass = ref(null) // the host row
const glassForm = reactive({ user: '', mode: 'generate', public_key: '' })
const glassError = ref('')
const privateKey = ref('')
function startGlass(h) {
  glass.value = h
  Object.assign(glassForm, { user: h.ssh_users[0] || '', mode: 'generate', public_key: '' })
  glassError.value = ''
  privateKey.value = ''
}
async function saveGlass() {
  glassError.value = ''
  busy.value = true
  try {
    const out = await api.put(`/api/hosts/${enc(glass.value.host)}/ssh-keys/break-glass`, glassForm.mode === 'generate'
      ? { user: glassForm.user, generate: true } : { user: glassForm.user, public_key: glassForm.public_key })
    privateKey.value = out.private_key || ''
    if (!privateKey.value) glass.value = null
    toast('Break-glass key set: apply to put it on the host')
    await changed(); query.refresh()
  } catch (e) { glassError.value = e.message } finally { busy.value = false }
}
function download() {
  const url = URL.createObjectURL(new Blob([privateKey.value], { type: 'application/octet-stream' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: `break-glass-${glass.value.host}` })
  a.click()
  URL.revokeObjectURL(url)
}
function closeGlass() { privateKey.value = ''; glass.value = null }
const short = (fp) => (fp || '').replace(/^SHA256:/, '').slice(0, 12)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>SSH keys</h1>
      <span class="h-tag">{{ keys.length }} keys · {{ hosts.length }} hosts</span>
      <span class="grow"></span>
      <button v-if="admin" class="btn primary" @click="startAdd"><Plus :size="15" /> Key</button>
    </div>

    <div v-if="!admin" class="card"><p class="muted">Only administrators manage SSH keys. A host's key state is on its Agent tab.</p></div>

    <template v-else>
      <div class="kpis">
        <button v-for="[k, label, tone, sub] in TILES" :key="k" class="kpi" :class="[tone && `t-${tone}`, { on: filter === k }]" :aria-pressed="filter === k" @click="filter = filter === k ? '' : k">
          <span class="label">{{ label }}</span><b>{{ counts[k] }}</b><span class="small muted">{{ sub }}</span>
        </button>
      </div>

      <div class="card flush">
        <div class="table-wrap"><table>
          <thead><tr><th>Host</th><th>State</th><th>Users</th><th class="num">Plan</th><th>Break-glass</th><th class="r"></th></tr></thead>
          <tbody>
            <template v-for="h in rows" :key="h.host">
              <tr class="clickable" @click="toggle(h.host)">
                <td><RouterLink :to="{ path: `/hosts/${h.host}`, query: { tab: 'agent' } }" class="mono" @click.stop>{{ h.host }}</RouterLink>
                  <div v-if="!h.linked" class="small warn">not linked</div></td>
                <td><span class="st" :class="STATES[h.state]?.[1]">{{ STATES[h.state]?.[0] || h.state }}</span>
                  <div v-if="h.issues.length" class="small faint">{{ h.issues[0] }}<template v-if="h.issues.length > 1"> (+{{ h.issues.length - 1 }})</template></div></td>
                <td class="mono small">{{ h.ssh_users.join(', ') || '—' }}</td>
                <td class="num mono small"><span v-if="h.add" class="ok">+{{ h.add }}</span> <span v-if="h.remove" class="bad">−{{ h.remove }}</span><span v-if="!h.add && !h.remove" class="faint">—</span></td>
                <td class="small"><span :class="h.break_glass_state === 'ok' ? 'ok' : 'warn'">{{ { ok: 'on the host', missing: 'none', not_on_host: 'not applied' }[h.break_glass_state] || '—' }}</span></td>
                <td class="r nowrap" @click.stop>
                  <button class="btn small ghost" :disabled="busy || !h.linked" @click="report(h)">Report</button>
                  <button class="btn small ghost" :disabled="busy || !h.ssh_users.length" @click="startGlass(h)">Break-glass</button>
                  <button class="btn small" :disabled="busy || !h.can_apply || !h.linked" :title="h.can_apply ? '' : 'ssh_keys must be on at both ends, with ssh_users in agent.conf'" @click="apply(h)">Apply</button>
                </td>
              </tr>
              <tr v-if="open === h.host" class="detail"><td colspan="6">
                <div v-if="!detail" class="faint small">Loading…</div>
                <div v-else>
                  <ul v-if="detail.issues.length" class="issues small"><li v-for="i in detail.issues" :key="i">{{ i }}</li></ul>
                  <div v-for="u in detail.users" :key="u.user" class="user">
                    <div class="u-head"><b class="mono">{{ u.user }}</b>
                      <span class="small faint">{{ u.managed ? 'managed by labdex' : 'not in ssh_users: reported only' }}</span></div>
                    <div v-for="k in u.keys" :key="k.sha256" class="small"><span :class="k.on_host ? 'ok' : 'warn'">{{ k.on_host ? 'on host' : 'to add' }}</span> {{ k.name }} <span class="mono faint">{{ short(k.sha256) }}</span></div>
                    <div v-for="fp in u.remove" :key="fp" class="small"><span class="bad">to remove</span> <span class="mono faint">{{ short(fp) }}</span></div>
                    <div v-for="k in u.own" :key="k.sha256 + k.file" class="small"><span class="warn">own file</span> {{ k.known || k.comment || 'unknown key' }}
                      <span class="mono faint">{{ short(k.sha256) }}</span><span v-if="k.options" class="faint"> (with options)</span></div>
                    <div v-for="e in u.errors" :key="e" class="small bad">{{ e }}</div>
                  </div>
                  <div v-if="detail.sshd.authorizedkeysfile" class="small faint mt-2">sshd reads <span class="mono">{{ detail.sshd.authorizedkeysfile }}</span>;
                    passwords <span class="mono">{{ detail.sshd.passwordauthentication || '?' }}</span>, root <span class="mono">{{ detail.sshd.permitrootlogin || '?' }}</span></div>
                </div>
              </td></tr>
            </template>
            <tr v-if="!rows.length"><td colspan="6" class="empty">
              <template v-if="hosts.length">Nothing matches.</template>
              <template v-else>No host has an agent yet. Install the Go agent with <span class="mono">--ssh-keys --ssh-users</span>.</template>
            </td></tr>
          </tbody>
        </table></div>
      </div>

      <div class="card flush mt-4">
        <div class="table-wrap"><table>
          <thead><tr><th>Key</th><th>Type</th><th>Fingerprint</th><th>May log in as</th><th class="r"></th></tr></thead>
          <tbody>
            <tr v-for="k in keys" :key="k.id">
              <td><b>{{ k.name }}</b><div v-if="k.owner" class="small faint">{{ k.owner }}</div></td>
              <td class="small mono">{{ k.type }}<template v-if="k.bits"> {{ k.bits }}</template></td>
              <td class="small"><Copy :text="k.sha256" /></td>
              <td>
                <span v-for="g in k.grants" :key="g.id" class="chip mono small">{{ g.user }}@{{ g.host === '*' ? 'every host' : g.host }}
                  <button class="x" :aria-label="`Remove ${g.user}@${g.host}`" :disabled="busy" @click="ungrant(k, g)"><X :size="12" /></button></span>
                <span v-if="!k.grants.length" class="faint small">nowhere yet</span>
              </td>
              <td class="r nowrap"><button class="btn small ghost" @click="startGrant(k)">Grant</button>
                <button class="btn small ghost danger" :disabled="busy" @click="removeKey(k)">Remove</button></td>
            </tr>
            <tr v-if="!keys.length"><td colspan="5" class="empty">
              <div class="e-ic"><KeyRound :size="18" /></div>
              <div class="e-t">No SSH keys yet</div>
              <p>Add people's public keys, say where each may log in, and apply. labdex never needs a private key.</p>
            </td></tr>
          </tbody>
        </table></div>
      </div>
      <p class="hint mt-3">labdex writes its keys to <span class="mono">/etc/ssh/labdex</span> on a host, for the users its
        <span class="mono">agent.conf</span> names in <span class="mono">ssh_users</span>, and only with <span class="mono">ssh_keys</span> on at both ends.
        <a href="https://labdex-app.github.io/labdex/guide/ssh-keys/" target="_blank" rel="noopener">How it works</a></p>
    </template>

    <Modal v-if="adding" title="Add an SSH key" :busy="busy" @close="adding = false">
      <Field label="Name" hint="What people call it, e.g. “alice laptop”."><input v-model.trim="form.name" type="text"></Field>
      <Field label="Owner" class="mt-3"><input v-model.trim="form.owner" type="text" placeholder="alice"></Field>
      <Field label="Public key" class="mt-3" :error="formError" hint="One line from the .pub file: ssh-ed25519 AAAA… comment. No options in front.">
        <textarea v-model.trim="form.public_key" rows="3" class="mono"></textarea></Field>
      <Field label="Note" class="mt-3"><input v-model="form.note" type="text"></Field>
      <template #foot><button class="btn ghost" @click="adding = false">Cancel</button>
        <button class="btn primary" :disabled="busy || !form.name || !form.public_key" @click="addKey">Add</button></template>
    </Modal>

    <Modal v-if="granting" :title="`Where may ${granting.name} log in?`" :busy="busy" @close="granting = null">
      <Field label="Host"><select v-model="grant.host"><option value="*">Every host that manages this user</option>
        <option v-for="n in hostNames" :key="n" :value="n">{{ n }}</option></select></Field>
      <Field label="As user" class="mt-3" :error="grantError" hint="It goes on a host only if its agent.conf lists this user in ssh_users.">
        <input v-model.trim="grant.user" type="text" class="mono" placeholder="admin"></Field>
      <template #foot><button class="btn ghost" @click="granting = null">Cancel</button>
        <button class="btn primary" :disabled="busy || !grant.user" @click="addGrant">Grant</button></template>
    </Modal>

    <Modal v-if="glass" :title="`Break-glass key for ${glass.host}`" :busy="busy" wide @close="closeGlass">
      <template v-if="!privateKey">
        <p class="small muted">For when labdex or the agent is down. An apply never removes it, it can only replace it.</p>
        <Field label="User" class="mt-3"><select v-model="glassForm.user"><option v-for="u in glass.ssh_users" :key="u">{{ u }}</option></select></Field>
        <div class="mt-3 radios">
          <label><input v-model="glassForm.mode" type="radio" value="generate"> Make a new key pair: labdex shows the private key once and keeps only the public key</label>
          <label><input v-model="glassForm.mode" type="radio" value="paste"> Use my own public key (made offline)</label>
        </div>
        <Field v-if="glassForm.mode === 'paste'" label="Public key" class="mt-3"><textarea v-model.trim="glassForm.public_key" rows="3" class="mono"></textarea></Field>
        <div v-if="glassError" class="err mt-2">{{ glassError }}</div>
      </template>
      <template v-else>
        <div class="warnbox">This is the only time labdex shows this private key. Keep it offline, in a password manager,
          not on a host or in labdex. Then apply, so the public half goes on {{ glass.host }}.</div>
        <textarea :value="privateKey" rows="9" class="mono mt-3" readonly aria-label="Private key"></textarea>
      </template>
      <template #foot>
        <template v-if="!privateKey"><button class="btn ghost" @click="closeGlass">Cancel</button>
          <button class="btn primary" :disabled="busy || !glassForm.user || (glassForm.mode === 'paste' && !glassForm.public_key)" @click="saveGlass">Set</button></template>
        <template v-else><button class="btn ghost" @click="download">Download</button>
          <button class="btn primary" @click="closeGlass">I have stored it</button></template>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.num { text-align: right; font-variant-numeric: tabular-nums; }
.r { text-align: right; }
.nowrap { white-space: nowrap; }
.clickable { cursor: pointer; }
.st { font-size: 12px; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.ok { color: var(--ok); }
.bad { color: var(--err); }
.warn { color: var(--warn); }
.detail td { background: var(--raise); }
.issues { margin: 0 0 8px; padding-left: 18px; color: var(--warn); }
.user + .user { margin-top: 10px; }
.u-head { display: flex; gap: 10px; align-items: baseline; }
.chip { display: inline-flex; align-items: center; gap: 4px; padding: 0 4px 0 8px; margin: 2px 4px 2px 0; border: 1px solid var(--line); border-radius: var(--r-tag); }
.chip .x { background: none; border: 0; padding: 2px; color: var(--muted); cursor: pointer; }
.radios { display: flex; flex-direction: column; gap: 6px; }
textarea[readonly] { width: 100%; }
</style>
