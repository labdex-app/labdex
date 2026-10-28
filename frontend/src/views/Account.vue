<script setup>
import { confirm } from '../confirm'
import { onMounted, reactive, ref } from 'vue'
import { api, ApiError } from '../api'
import Copy from '../components/Copy.vue'
import Field from '../components/Field.vue'
import { ago, loadAuth, state, toast } from '../store'

const user = state.auth.user
const pw = reactive({ current: '', new: '', again: '' })
const pwErrors = ref({})
const tokens = ref([])
const tok = reactive({ name: '', read_only: false, expires_days: 90 })
const created = ref(null)

async function loadTokens() { tokens.value = await api.get('/api/auth/tokens') }
onMounted(loadTokens)

async function changePassword() {
  pwErrors.value = {}
  if (pw.new !== pw.again) { pwErrors.value = { again: "Doesn't match" }; return }
  try {
    await api.post('/api/auth/password', { current: pw.current, new: pw.new })
    Object.assign(pw, { current: '', new: '', again: '' })
    toast('Password changed; other sessions are signed out')
    await loadAuth()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) pwErrors.value = e.fields
    else toast(e.message, 'error')
  }
}

async function createToken() {
  try {
    created.value = await api.post('/api/auth/tokens', { ...tok, expires_days: tok.expires_days || null })
    tok.name = ''
    await loadTokens()
  } catch (e) { toast(e.message, 'error') }
}

async function revoke(t) {
  if (!(await confirm({ title: `Revoke the token “${t.name}”?`, body: `Anything using it stops working.`, action: 'Revoke', danger: true }))) return
  await api.del(`/api/auth/tokens/${t.id}`)
  await loadTokens()
  toast('Token revoked')
}

const PERM_LABEL = { host: 'Hosts', network: 'Networks', alias: 'Aliases', integration: 'Plugins', discovery: 'Discovery', audit: 'History' }
</script>

<template>
  <div class="page">
    <div class="page-head"><h1>{{ user.display_name }}</h1><span v-if="user.username !== user.display_name" class="sub mono">{{ user.username }}</span></div>
    <div class="split">
      <div>
        <div class="card">
          <h2>API tokens</h2>
          <p class="muted small" style="margin-top: 0">For scripts, Ansible and Prometheus. Send as <code>Authorization: Bearer ldx_…</code>. A token acts as you, with your permissions; a read-only token can never change anything.</p>
          <div v-if="created" class="warnbox" style="margin-bottom: 14px">
            <div class="small" style="margin-bottom: 6px"><b>Copy “{{ created.name }}” now.</b> It isn't shown again.</div>
            <Copy :text="created.token" />
          </div>
          <div class="table-wrap" v-if="tokens.length">
            <table>
              <thead><tr><th>Name</th><th>Token</th><th>Access</th><th>Expires</th><th>Last used</th><th></th></tr></thead>
              <tbody>
                <tr v-for="t in tokens" :key="t.id">
                  <td>{{ t.name }}</td><td class="mono faint">{{ t.prefix }}</td>
                  <td>{{ t.read_only ? 'read-only' : 'read & write' }}</td>
                  <td class="small">{{ t.expires_at ? new Date(t.expires_at).toLocaleDateString() : 'never' }}</td>
                  <td class="small">{{ ago(t.last_used_at) }}</td>
                  <td class="right"><button class="btn small danger" @click="revoke(t)">Revoke</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          <form class="row" style="margin-top: 14px" @submit.prevent="createToken">
            <input v-model.trim="tok.name" type="text" placeholder="Name, e.g. prometheus" style="flex: 1; min-width: 180px" required>
            <select v-model.number="tok.expires_days" class="auto">
              <option :value="30">30 days</option><option :value="90">90 days</option><option :value="365">1 year</option><option :value="0">never expires</option>
            </select>
            <label class="check small"><input v-model="tok.read_only" type="checkbox"> read-only</label>
            <button class="btn primary">Create token</button>
          </form>
        </div>

        <form v-if="user.has_password && user.via === 'session'" class="card" @submit.prevent="changePassword">
          <h2>Password</h2>
          <div class="grid3">
            <Field label="Current" :error="pwErrors.current"><input v-model="pw.current" type="password" autocomplete="current-password" required></Field>
            <Field label="New" :error="pwErrors.new" hint="10+ characters"><input v-model="pw.new" type="password" autocomplete="new-password" required></Field>
            <Field label="New again" :error="pwErrors.again"><input v-model="pw.again" type="password" autocomplete="new-password" required></Field>
          </div>
          <button class="btn primary" style="margin-top: 12px">Change password</button>
        </form>
      </div>
      <aside class="card">
        <h3>What you can do</h3>
        <p v-if="user.is_admin" class="small">Administrator: everything.</p>
        <dl v-else class="kv">
          <template v-for="(acts, type) in user.permissions" :key="type">
            <dt>{{ PERM_LABEL[type] || type }}</dt>
            <dd class="small">{{ acts.length ? acts.join(', ') : '—' }}</dd>
          </template>
        </dl>
        <p class="hint">Somewhere in the inventory, at least. Roles can be limited to zones, networks, tags or names, so some objects may still be read-only. Groups: {{ user.groups.join(', ') || 'none' }}.</p>
      </aside>
    </div>
  </div>
</template>
