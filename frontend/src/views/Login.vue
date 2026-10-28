<script setup>
import { computed, reactive, ref } from 'vue'
import { api, ApiError } from '../api'
import Field from '../components/Field.vue'
import { state } from '../store'

const emit = defineEmits(['done'])
const setup = computed(() => state.auth?.setup_required)
const demo = computed(() => state.auth?.demo)
const form = reactive({ username: state.auth?.demo?.username || '', password: state.auth?.demo?.password || '', password2: '' })
const errors = ref({})
// the provider sent the browser back with a problem (labdex puts it in ?sso_error=)
const sp = new URLSearchParams(window.location.search)
const message = ref(sp.get('sso_error') ? `Single sign-on: ${sp.get('sso_error')}` : '')
if (sp.get('sso_error')) window.history.replaceState(null, '', window.location.pathname)
const oidc = computed(() => (!setup.value && state.auth?.oidc?.enabled ? state.auth.oidc : null))
const passwordForm = computed(() => setup.value || !oidc.value?.only)
const ssoHref = computed(() => `/api/auth/oidc/login?next=${encodeURIComponent(window.location.pathname === '/' ? '/' : window.location.pathname + window.location.search)}`)
const busy = ref(false)

async function submit() {
  errors.value = {}
  message.value = ''
  if (setup.value && form.password !== form.password2) {
    errors.value = { password2: "The passwords don't match" }
    return
  }
  busy.value = true
  try {
    await api.post(setup.value ? '/api/auth/setup' : '/api/auth/login', { username: form.username, password: form.password })
    emit('done')
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    else message.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="wrap">
    <form class="card box" @submit.prevent="submit">
      <div class="brand">lab<span>dex</span></div>
      <template v-if="setup">
        <h1>Welcome</h1>
        <p class="muted">Nobody has signed in yet. Create the first administrator; you can add more users afterwards.</p>
      </template>
      <h1 v-else>Sign in</h1>
      <div v-if="demo && !setup" class="warnbox small mb-4">
        This is the labdex demo. Sign in as <b class="mono">{{ demo.username }}</b> with <b class="mono">{{ demo.password }}</b>
        (filled in). Change anything you like: it all resets every night at 03:00 UTC, and nothing here talks to a real network.
      </div>

      <div v-if="message" class="errbox m-0 mt-2 mb-1">{{ message }}</div>
      <a v-if="oidc" :href="ssoHref" class="btn primary go sso">Sign in with {{ oidc.name }}</a>
      <div v-if="oidc && passwordForm" class="or small faint"><span>or with a password</span></div>
      <div v-if="passwordForm" class="stack">
        <Field label="Username" :error="errors.username">
          <input v-model.trim="form.username" type="text" autocomplete="username" autofocus required spellcheck="false">
        </Field>
        <Field label="Password" :error="errors.password" :hint="setup ? 'At least 10 characters' : ''">
          <input v-model="form.password" type="password" :autocomplete="setup ? 'new-password' : 'current-password'" required>
        </Field>
        <Field v-if="setup" label="Password again" :error="errors.password2">
          <input v-model="form.password2" type="password" autocomplete="new-password" required>
        </Field>
      </div>
      <button v-if="passwordForm" class="btn go" :class="{ primary: !oidc }" :disabled="busy">{{ busy ? '…' : setup ? 'Create administrator' : 'Sign in' }}</button>
      <p v-if="state.auth?.sso && !setup" class="hint">Single sign-on is configured: open labdex through your SSO proxy to be signed in automatically.</p>
    </form>
  </main>
</template>

<style scoped>
.wrap { min-height: 100vh; display: grid; place-items: center; padding: 16px; border-top: 3px solid var(--accent); }
.box { width: min(400px, 100%); padding: 28px; }
.brand { font-weight: 800; font-size: 20px; margin-bottom: 18px; }
.brand span { color: var(--accent-soft); }
h1 { margin-bottom: 8px; }
.stack { margin-top: 16px; }
.go { width: 100%; justify-content: center; margin-top: 20px; padding: 10px; }
.sso { display: flex; margin-top: 8px; text-decoration: none; }
.or { display: flex; align-items: center; gap: 10px; margin-top: 18px; }
.or::before, .or::after { content: ""; flex: 1; border-top: 1px solid var(--line); }
</style>
