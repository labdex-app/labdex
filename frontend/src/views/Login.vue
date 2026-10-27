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
const message = ref('')
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
      <div v-if="demo && !setup" class="warnbox small" style="margin-bottom: 14px">
        This is the labdex demo. Sign in as <b class="mono">{{ demo.username }}</b> with <b class="mono">{{ demo.password }}</b>
        (filled in). Change anything you like: it all resets every night at 03:00 UTC, and nothing here talks to a real network.
      </div>

      <div class="stack">
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
      <div v-if="message" class="errbox" style="margin-top: 14px">{{ message }}</div>
      <button class="btn primary go" :disabled="busy">{{ busy ? '…' : setup ? 'Create administrator' : 'Sign in' }}</button>
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
</style>
