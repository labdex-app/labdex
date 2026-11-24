<script setup>
// Sign in, and the first run. Signing in: what this is on the left, the form on the right.
// The first run is a short set-up in steps: the administrator first (the only step that must
// happen here), then where to go next: sites and their git source, plugins, import, agents.
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Check } from 'lucide-vue-next'
import { api, ApiError } from '../api'
import mark from '../assets/mark.svg'
import Field from '../components/Field.vue'
import AdminImport from '../components/AdminImport.vue'
import Marketplace from '../components/Marketplace.vue'
import { loadMeta, state } from '../store'

const props = defineProps({ signedIn: { type: Function, required: true } }) // loads labdex; throws if it can't
const emit = defineEmits(['done'])
const router = useRouter()
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
const host = window.location.host
const created = ref('') // the first administrator, once made: the set-up moves on to what's next
const step = ref(0)
const site = reactive({ existing: false, code: '', name: '', url: '', branch: 'main', mode: 'mr', path: '' })
const savedSite = ref(false)
const synced = ref(null)
watch(() => [site.name, site.url, site.branch, site.mode, site.path], () => { savedSite.value = false; synced.value = null })
const SYNC = { pulled: 'Connected · read the site from git', pushed: 'Connected · wrote the site to git', none: 'Connected · nothing to do', conflict: 'Both sides have changes', error: "Couldn't sync" }

const STEPS = [
  { title: 'Administrator', sub: 'the first account' },
  { title: 'Sites', sub: 'name and git source', to: '/admin/sites', what: 'A site is one place you run things, like home. Give it a git repository and labdex keeps the inventory there.' },
  { title: 'Plugins', sub: 'Proxmox, OPNsense …', to: '/plugins', what: 'Install the plugins for what you run, then add an integration for each system.' },
  { title: 'Import', sub: 'from other tools or a file', to: '/admin/import', what: 'Bring in hosts and networks you already have, from a file or another tool.' },
  { title: 'Agents', sub: 'on your hosts', to: '/hosts', what: 'A host enrols its agent from its own Agent tab, with a one-time token.' },
]

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
    if (setup.value) {
      created.value = form.username.toLowerCase()
      await afterAdmin()
    } else await props.signedIn() // the form stays, busy, until labdex is loaded
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    else message.value = problem(e)
  } finally {
    busy.value = false
  }
}

// A wrong password has the server's own words. A bare status (a proxy's 404, a crash) or no answer at all
// gets a sentence that says it's the server, not the person.
function problem(e) {
  if (!(e instanceof ApiError)) return e.name === 'TypeError' ? `Can't reach labdex: ${e.message}` : e.message
  if (e.body?.error) return e.message
  return `labdex answered with ${e.message}. Try again in a moment; if it keeps happening, check the server's log.`
}

// signed in now: the meta the embedded pages need, and the site labdex started with
async function afterAdmin() {
  await loadMeta().catch(() => {})
  const r = await api.get('/api/sites').catch(() => null)
  const first = r?.sites?.[0]
  if (first) Object.assign(site, { existing: true, code: first.code, name: first.name || '', url: first.git?.url || '',
    branch: first.git?.branch || 'main', mode: first.git?.mode || 'mr', path: first.git?.path || '' })
  step.value = 1
}

async function saveSite() {
  errors.value = {}
  message.value = ''
  busy.value = true
  const data = { code: site.code, name: site.name, git: site.url ? { url: site.url, branch: site.branch, mode: site.mode, path: site.path } : {} }
  try {
    if (site.existing) await api.put(`/api/sites/${encodeURIComponent(site.code)}`, data)
    else await api.post('/api/sites', data)
    site.existing = true
    savedSite.value = true
    await loadMeta().catch(() => {})
    if (site.url) await syncSite()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    else message.value = e.message
  } finally {
    busy.value = false
  }
}

// the first sync takes whichever side has something: an empty labdex reads the file from git
async function syncSite() {
  busy.value = true
  try {
    synced.value = await api.post(`/api/sites/${encodeURIComponent(site.code)}/git/sync`, {})
  } catch (e) {
    synced.value = { action: 'error', error: e.message }
  } finally {
    busy.value = false
  }
}

function go(to) {
  emit('done')
  router.push(to)
}
</script>

<template>
  <!-- first run: the set-up steps -->
  <main v-if="setup" class="first">
    <div class="card setup">
      <aside class="steps">
        <div class="s-brand"><img :src="mark" alt="" width="22" height="22"><span>Set up labdex</span></div>
        <ol>
          <li v-for="(s, i) in STEPS" :key="s.title" :class="{ done: i < step, now: i === step }" :aria-current="i === step ? 'step' : undefined">
            <span class="s-n" aria-hidden="true"><Check v-if="i < step" :size="13" /><template v-else>{{ i + 1 }}</template></span>
            <span><b>{{ s.title }}</b><span class="small muted">{{ i === 0 && created ? `${created}, created` : s.sub }}</span></span>
          </li>
        </ol>
      </aside>
      <form v-if="step === 0" class="s-body" @submit.prevent="submit">
        <div class="label">Step 1 of {{ STEPS.length }}</div>
        <h1>The first administrator</h1>
        <p class="muted m-0">Nobody has signed in yet. You can add more people, groups and single sign-on afterwards.</p>
        <div v-if="message" class="errbox mt-3 mb-0">{{ message }}</div>
        <div class="grid2">
          <Field label="Username" :error="errors.username">
            <input v-model.trim="form.username" type="text" autocomplete="username" autofocus required spellcheck="false">
          </Field>
          <span></span>
          <Field label="Password" :error="errors.password" hint="At least 10 characters">
            <input v-model="form.password" type="password" autocomplete="new-password" required>
          </Field>
          <Field label="Password again" :error="errors.password2">
            <input v-model="form.password2" type="password" autocomplete="new-password" required>
          </Field>
        </div>
        <div class="s-foot"><span class="grow"></span>
          <button class="btn primary" :disabled="busy">{{ busy ? 'Creating…' : 'Create administrator' }}</button></div>
      </form>
      <!-- steps 2 to 5: each can be skipped and done later on its own page -->
      <form v-else-if="step === 1" class="s-body" @submit.prevent="savedSite ? (step = 2) : saveSite()">
        <div class="label">Step 2 of {{ STEPS.length }}</div>
        <h1>{{ site.existing ? 'Your first site' : 'A site' }}</h1>
        <p class="muted m-0">A site is one place you run things, like home or public. Its inventory can live in a git repository.</p>
        <div class="grid2">
          <Field label="Code" :error="errors.code" :hint="site.existing ? 'Set when labdex started' : 'Lowercase, used in names'">
            <input v-model.trim="site.code" :readonly="site.existing" class="mono" spellcheck="false"></Field>
          <Field label="Name" :error="errors.name"><input v-model.trim="site.name" placeholder="Home"></Field>
        </div>
        <Field label="Git repository" :error="errors['git.url']" hint="Optional. An https:// address; a token for a private repository goes in the vault, on the Sites page.">
          <input v-model.trim="site.url" type="text" inputmode="url" placeholder="https://git.example.com/lab/infra.git" spellcheck="false"></Field>
        <div v-if="site.url" class="grid2">
          <Field label="Branch" :error="errors['git.branch']"><input v-model.trim="site.branch" class="mono" spellcheck="false"></Field>
          <Field label="Changes" :error="errors['git.mode']">
            <select v-model="site.mode"><option value="mr">Merge request for every change</option><option value="commit">Commit to the branch</option></select></Field>
          <Field label="File" :error="errors['git.path']"><input v-model.trim="site.path" class="mono" :placeholder="`labdex/${site.code || 'home'}.yaml`" spellcheck="false"></Field>
        </div>
        <div v-if="synced" class="conn small" :class="synced.action === 'error' || synced.action === 'conflict' ? 'bad' : ''">
          <span class="dot" :class="synced.action === 'error' || synced.action === 'conflict' ? 'err' : 'ok'"></span>
          <span>{{ SYNC[synced.action] || synced.action }}<template v-if="synced.commit"> · <span class="mono">{{ synced.commit.slice(0, 7) }}</span></template>
            <template v-if="synced.error">: {{ synced.error }}</template></span>
        </div>
        <div v-if="message" class="errbox mt-3 mb-0">{{ message }}</div>
        <div class="s-foot"><span class="grow"></span>
          <button type="button" class="btn ghost" @click="step = 2">Skip for now</button>
          <button v-if="savedSite && site.url" type="button" class="btn" :disabled="busy" @click="syncSite">{{ busy ? 'Syncing…' : 'Sync now' }}</button>
          <button v-if="savedSite" type="button" class="btn primary" @click="step = 2">Next</button>
          <button v-else class="btn primary" :disabled="busy || !site.code">{{ busy ? 'Saving…' : 'Save site' }}</button></div>
      </form>
      <section v-else-if="step === 2" class="s-body">
        <div class="label">Step 3 of {{ STEPS.length }}</div>
        <h1>Plugins</h1>
        <p class="muted m-0">Install the plugins for what you run. Each asks what it may do before it's installed; you add an integration for each system afterwards.</p>
        <div class="embed"><Marketplace /></div>
        <div class="s-foot"><button type="button" class="btn" @click="step = 1">Back</button><span class="grow"></span>
          <button type="button" class="btn primary" @click="step = 3">Next</button></div>
      </section>
      <section v-else-if="step === 3" class="s-body">
        <div class="label">Step 4 of {{ STEPS.length }}</div>
        <h1>Import</h1>
        <p class="muted m-0">Bring in the networks and hosts you already have. A preview first; nothing that exists is changed.</p>
        <div class="embed"><AdminImport /></div>
        <div class="s-foot"><button type="button" class="btn" @click="step = 2">Back</button><span class="grow"></span>
          <button type="button" class="btn primary" @click="step = 4">Next</button></div>
      </section>
      <section v-else class="s-body">
        <div class="label">Step 5 of {{ STEPS.length }}</div>
        <h1>Agents</h1>
        <p class="muted m-0">An agent on a Linux host reports what runs there, its updates, and its CPU, memory and disk. It only reads until you allow more, on both ends.</p>
        <ol class="howto">
          <li>Open a host and its <b>Agent</b> tab.</li>
          <li><b>Make an enrol token</b>: it works once and expires.</li>
          <li>Run the install line it shows on the host. The agent dials out to labdex; the host needs no open port.</li>
        </ol>
        <div class="s-foot"><button type="button" class="btn" @click="step = 3">Back</button><span class="grow"></span>
          <button type="button" class="btn" @click="go('/hosts')">Open Hosts</button>
          <button type="button" class="btn primary" @click="go('/')">Open labdex</button></div>
      </section>
    </div>
  </main>

  <!-- sign in -->
  <main v-else class="signin">
    <section class="intro" aria-hidden="true">
      <div class="i-mid">
        <img :src="mark" alt="" width="64" height="64" class="i-mark">
        <div class="i-name">labdex</div>
        <p class="i-tag">An inventory for your homelab that treats zones and names as first-class, and generates what the rest of the lab needs.</p>
        <div class="i-tags"><span class="h-tag">self-hosted</span><span class="h-tag">git as source</span><span class="h-tag">open source</span></div>
      </div>
      <div class="i-foot mono">{{ host }}</div>
    </section>
    <section class="side">
      <form class="f" @submit.prevent="submit">
        <h1>Sign in</h1>
        <p class="muted m-0 sub">to labdex at {{ host }}</p>
        <div v-if="demo" class="warnbox small mt-3 mb-0">
          This is the labdex demo. Sign in as <b class="mono">{{ demo.username }}</b> with <b class="mono">{{ demo.password }}</b>
          (filled in). Change anything you like: it all resets every night at 03:00 UTC, and nothing here talks to a real network.
        </div>
        <div v-if="message" class="errbox mt-3 mb-0" role="alert">{{ message }}</div>
        <a v-if="oidc && !passwordForm" :href="ssoHref" class="btn primary go sso">Sign in with {{ oidc.name }}</a>
        <div v-if="passwordForm" class="stack">
          <Field label="Username" :error="errors.username">
            <input v-model.trim="form.username" type="text" autocomplete="username" autofocus required spellcheck="false">
          </Field>
          <Field label="Password" :error="errors.password">
            <input v-model="form.password" type="password" autocomplete="current-password" required>
          </Field>
          <button class="btn primary go" :disabled="busy">{{ busy ? 'Signing in…' : 'Sign in' }}</button>
        </div>
        <template v-if="oidc && passwordForm">
          <div class="or small faint"><span>or</span></div>
          <a :href="ssoHref" class="btn go sso">Continue with {{ oidc.name }}</a>
        </template>
        <p v-if="state.auth?.sso" class="hint mt-3">Single sign-on is configured: open labdex through your SSO proxy to be signed in automatically.</p>
      </form>
    </section>
  </main>
</template>

<style scoped>
.signin { min-height: 100vh; display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(360px, 1fr); background: var(--card); }
.intro { position: relative; display: flex; flex-direction: column; justify-content: center; padding: 48px 90px; border-right: 1px solid var(--line);
  background-color: var(--bg); background-image: radial-gradient(var(--dot) 1px, transparent 1.2px); background-size: 22px 22px; }
.i-mark { border-radius: 14px; }
.i-name { font: 600 calc(46px * var(--text))/1.1 var(--disp); letter-spacing: -.03em; margin: 26px 0 14px; }
.i-tag { font-size: calc(17px * var(--text)); color: var(--muted); max-width: 46ch; margin: 0 0 24px; }
.i-tags { display: flex; gap: 8px; flex-wrap: wrap; }
.i-foot { position: absolute; left: 90px; bottom: 28px; color: var(--faint); font-size: calc(12px * var(--text)); }
.side { display: grid; place-items: center; padding: 32px; }
.f { width: min(400px, 100%); }
.f h1 { font-size: calc(24px * var(--text)); }
.sub { margin-top: 4px !important; }
.stack { margin-top: 22px; display: flex; flex-direction: column; gap: 4px; }
.go { width: 100%; justify-content: center; margin-top: 14px; padding: 10px; display: flex; text-decoration: none; }
.or { display: flex; align-items: center; gap: 10px; margin-top: 18px; }
.or::before, .or::after { content: ""; flex: 1; border-top: 1px solid var(--line); }
@media (max-width: 900px) {
  .signin { grid-template-columns: minmax(0, 1fr); }
  .intro { padding: 32px 24px 28px; border-right: 0; border-bottom: 1px solid var(--line); }
  .i-name { font-size: calc(32px * var(--text)); margin: 16px 0 8px; }
  .i-mark { width: 44px; height: 44px; }
  .i-tag { font-size: calc(15px * var(--text)); margin-bottom: 14px; }
  .i-foot { display: none; }
}

.first { min-height: 100vh; display: grid; place-items: center; padding: 16px;
  background-color: var(--bg); background-image: radial-gradient(var(--dot) 1px, transparent 1.2px); background-size: 22px 22px; }
.setup { width: min(900px, 100%); display: grid; grid-template-columns: 260px minmax(0, 1fr); padding: 0; overflow: hidden; }
.steps { padding: 26px 24px; border-right: 1px solid var(--line); background: var(--panel); }
.s-brand { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: calc(15px * var(--text)); margin-bottom: 22px; }
.s-brand img { border-radius: 6px; }
.steps ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 18px; }
.steps li { display: flex; gap: 12px; align-items: flex-start; color: var(--muted); }
.steps li > span:last-child { display: flex; flex-direction: column; }
.steps li b { font-weight: 500; }
.steps li.now, .steps li.done { color: var(--ink); }
.s-n { flex: none; width: 24px; height: 24px; border-radius: 50%; border: 1.5px solid var(--line-strong); display: grid; place-items: center;
  font: calc(11px * var(--text)) var(--mono); color: var(--faint); }
.now .s-n { border-color: var(--accent); color: var(--accent-soft); }
.done .s-n { border-color: var(--ok); background: var(--ok); color: var(--card); }
.s-body { padding: 28px 30px; display: flex; flex-direction: column; }
.s-body h1 { margin: 6px 0 6px; font-size: calc(22px * var(--text)); }
.grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 14px; margin-top: 20px; }
.s-foot { display: flex; gap: 10px; align-items: center; margin-top: 18px; }
.next { list-style: none; margin: 18px 0 0; padding: 0; display: flex; flex-direction: column; }
.next li { display: flex; gap: 12px; align-items: center; padding: 12px 0; border-top: 1px solid var(--line); }
.next li:last-child { border-bottom: 1px solid var(--line); }
.nx-t { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.s-body > .field, .s-body > label { margin-top: 6px; }
.conn { display: flex; gap: 8px; align-items: center; margin-top: 14px; padding: 10px 12px; border: 1px solid var(--line); border-radius: var(--r-box); background: var(--panel); }
.conn.bad { border-color: var(--err); }
.embed { margin-top: 16px; max-height: 420px; overflow: auto; border-top: 1px solid var(--line); padding-top: 12px; }
.howto { margin: 16px 0 0; padding-left: 20px; display: flex; flex-direction: column; gap: 8px; }
@media (max-width: 760px) {
  .setup { grid-template-columns: minmax(0, 1fr); }
  .steps { border-right: 0; border-bottom: 1px solid var(--line); }
  .steps ol { flex-direction: row; flex-wrap: wrap; gap: 10px 16px; }
  .steps li > span:last-child .small { display: none; }
  .grid2 { grid-template-columns: minmax(0, 1fr); }
  .grid2 > span:empty { display: none; }
}
</style>
