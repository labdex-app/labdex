<script setup>
import { confirm } from '../confirm'
import { computed, onMounted, reactive, ref } from 'vue'
import { api, ApiError } from '../api'
import Field from '../components/Field.vue'
import Modal from '../components/Modal.vue'
import { Copy as CopyIcon, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import AdminApprovals from '../components/AdminApprovals.vue'
import AdminImport from '../components/AdminImport.vue'
import AdminNotifications from '../components/AdminNotifications.vue'
import AdminPlugins from '../components/AdminPlugins.vue'
import { ago, changed, state, toast } from '../store'

defineProps({ tab: String })
const GROUPS = [
  ['Instance', [['general', 'General']]],
  ['People', [['users', 'Users'], ['groups', 'Groups'], ['roles', 'Roles'], ['access', 'Access']]],
  ['Operate', [['approvals', 'Approvals & windows'], ['notifications', 'Notifications']]],
  ['Extensions', [['plugins', 'Plugins']]],
  ['Data', [['backup', 'Backup & restore'], ['import', 'Add from other tools']]],
]

const users = ref([])
const sso = ref(null)
const groups = ref([])
const roles = ref([])
const types = ref([])
const actions = ref([])
const bindings = ref([])
const grants = ref([])
const errors = ref({})

async function load() {
  const [u, g, r, b, gr] = await Promise.all(['users', 'groups', 'roles', 'bindings', 'grants'].map((x) => api.get(`/api/admin/${x}`)))
  users.value = u; groups.value = g; roles.value = r.roles; types.value = r.object_types; actions.value = r.actions
  bindings.value = b; grants.value = gr
}
onMounted(() => { load().catch((e) => toast(e.message, 'error')); api.get('/api/admin/sso').then((d) => { sso.value = d }).catch(() => {}) })

async function call(fn, ok) {
  errors.value = {}
  try {
    const r = await fn()
    if (ok) toast(ok)
    await load()
    return r ?? true
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
    return false
  }
}

// ---------------------------------------------------------------- users
const nu = reactive({ username: '', display_name: '', password: '', is_admin: false, groups: [] })
const editUser = ref(null)
const newUserOpen = ref(false)
async function createUser() {
  if (await call(() => api.post('/api/admin/users', nu), `Created ${nu.username}`)) { Object.assign(nu, { username: '', display_name: '', password: '', is_admin: false, groups: [] }); newUserOpen.value = false }
}
function startEditUser(u) { errors.value = {}; editUser.value = { ...u, groups: [...u.groups], password: '' } }
async function saveUser() {
  const u = editUser.value
  const body = { display_name: u.display_name, is_admin: u.is_admin, active: u.active, groups: u.groups }
  if (u.password) body.password = u.password
  if (await call(() => api.put(`/api/admin/users/${u.id}`, body), `Saved ${u.username}`)) editUser.value = null
}
async function deleteUser(u) {
  if ((await confirm({ title: `Delete ${u.username}?`, body: `Their tokens stop working. History keeps their name.`, action: 'Delete', danger: true }))) await call(() => api.del(`/api/admin/users/${u.id}`), `Deleted ${u.username}`)
}
const rolesOf = (kind, name) => bindings.value.filter((b) => b[kind] === name)

// --------------------------------------------------------------- groups
const editGroup = ref(null)
function startGroup(g) { errors.value = {}; editGroup.value = g ? { ...g, members: [...g.members] } : { name: '', description: '', members: [] } }
async function saveGroup() {
  const g = editGroup.value
  const ok = await call(() => (g.id ? api.put(`/api/admin/groups/${g.id}`, g) : api.post('/api/admin/groups', g)), `Saved ${g.name}`)
  if (ok) editGroup.value = null
}
async function deleteGroup(g) {
  if ((await confirm({ title: `Delete group ${g.name}?`, body: `Its role bindings go with it.`, action: 'Delete', danger: true }))) await call(() => api.del(`/api/admin/groups/${g.id}`), `Deleted ${g.name}`)
}

// ---------------------------------------------------------------- roles
const editRole = ref(null)
function startRole(r) { errors.value = {}; editRole.value = r ? { ...r, permissions: [...r.permissions] } : { name: '', description: '', permissions: [] } }
function hasPerm(t, a) { return editRole.value.permissions.includes(`${t}:${a}`) }
function togglePerm(t, a) {
  const p = `${t}:${a}`, list = editRole.value.permissions
  editRole.value.permissions = list.includes(p) ? list.filter((x) => x !== p) : [...list, p]
}
async function saveRole() {
  const r = editRole.value
  if (await call(() => (r.id ? api.put(`/api/admin/roles/${r.id}`, r) : api.post('/api/admin/roles', r)), `Saved ${r.name}`)) editRole.value = null
}
async function deleteRole(r) {
  if ((await confirm({ title: `Delete role ${r.name}?`, body: `Bindings that use it go with it.`, action: 'Delete', danger: true }))) await call(() => api.del(`/api/admin/roles/${r.id}`), `Deleted ${r.name}`)
}

// --------------------------------------------------------------- access
const bindOpen = ref(false)
const grantOpen = ref(false)
async function removeBinding(b) {
  if (await confirm({ title: `Remove ${b.role} from ${b.user || b.group}?`, action: 'Remove', danger: true })) call(() => api.del(`/api/admin/bindings/${b.id}`), 'Removed')
}
async function removeGrant(g) {
  if (await confirm({ title: `Remove the grant on ${g.object_name}?`, body: `${g.user || g.group} loses ${g.actions.join(', ')}.`, action: 'Remove', danger: true })) call(() => api.del(`/api/admin/grants/${g.id}`), 'Removed')
}
const nb = reactive({ who: 'group', subject: '', role: 'viewer', scopes: '*' })
async function createBinding() {
  const body = { [nb.who]: nb.subject, role: nb.role, scopes: nb.scopes.split(/[\s,]+/).filter(Boolean) }
  if (await call(() => api.post('/api/admin/bindings', body), 'Role granted')) { nb.scopes = '*'; bindOpen.value = false }
}
const ng = reactive({ who: 'user', subject: '', object_type: 'host', object_name: '', actions: ['view', 'edit'] })
async function createGrant() {
  const body = { [ng.who]: ng.subject, object_type: ng.object_type, object_name: ng.object_name, actions: ng.actions }
  if (await call(() => api.post('/api/admin/grants', body), 'Grant added')) { ng.object_name = ''; grantOpen.value = false }
}
const subjects = (who) => (who === 'user' ? users.value.map((u) => u.username) : groups.value.map((g) => g.name))

// ---------------------------------------------------------- conventions
const settings = reactive({ ...state.meta.settings })
async function saveSettings() {
  errors.value = {}
  try {
    await api.put('/api/admin/settings', settings)
    toast('Conventions saved')
    await changed()
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
  }
}

// --------------------------------------------------------------- backup
const restoreText = ref('')
const restoreErrors = ref([])
const busy = ref(false)
async function pickFile(ev) { const f = ev.target.files?.[0]; if (f) restoreText.value = await f.text() }
async function restore() {
  if (!restoreText.value.trim()) return
  if (!(await confirm({ title: 'Replace the whole inventory with this file?', body: 'Users, roles and integrations stay. Download a backup first if unsure.', action: 'Replace inventory', danger: true }))) return
  busy.value = true
  restoreErrors.value = []
  try {
    const r = await api.post('/api/admin/restore', { yaml: restoreText.value, confirm: 'replace' })
    toast(`Restored ${r.imported.networks} networks, ${r.imported.hosts} hosts, ${r.imported.aliases} aliases`)
    restoreText.value = ''
    await changed()
    Object.assign(settings, state.meta.settings)
  } catch (e) {
    restoreErrors.value = e instanceof ApiError && e.errors.length ? e.errors : [{ field: 'file', message: e.message }]
  } finally {
    busy.value = false
  }
}
const me = computed(() => state.auth.user.username)
</script>

<template>
  <div class="page">
    <div class="page-head"><h1>Settings</h1><span class="sub">labdex {{ state.meta.version }}</span></div>
    <div class="adm">
    <nav class="subnav" aria-label="Admin sections">
      <template v-for="[g, items] in GROUPS" :key="g">
        <div class="sg">{{ g }}</div>
        <RouterLink v-for="[k, l] in items" :key="k" :to="`/admin/${k}`" class="si">{{ l }}</RouterLink>
      </template>
    </nav>
    <div class="admin-body">

    <!-- ============================================================ users -->
    <section v-if="tab === 'users'">
      <div class="sect-head">
        <div><h2>Users</h2><p class="muted small lead">People who sign in with a password or through SSO. Roles usually come from their groups.</p></div>
        <span class="grow"></span>
        <button class="btn primary" @click="newUserOpen = true"><Plus :size="15" /> User</button>
      </div>
      <div class="card flush">
        <div class="table-wrap">
          <table>
            <thead><tr><th>User</th><th>Groups</th><th>Roles</th><th>Last sign-in</th><th></th></tr></thead>
            <tbody>
              <tr v-for="u in users" :key="u.id" :class="{ faint: !u.active }">
                <td><b>{{ u.display_name }}</b> <span v-if="u.username !== u.display_name" class="mono small faint">{{ u.username }}</span>
                  <span v-if="u.is_admin" class="tag">admin</span><span v-if="!u.active" class="tag">disabled</span>
                  <span v-if="!u.has_password" class="tag" title="Signs in through SSO only">sso</span></td>
                <td class="small">{{ u.groups.join(', ') || '—' }}</td>
                <td class="small">{{ rolesOf('user', u.username).map((b) => b.role).join(', ') || '—' }}</td>
                <td class="small">{{ ago(u.last_login) }}</td>
                <td class="right nowrap">
                  <button class="btn icon" title="Edit user" aria-label="Edit user" @click="startEditUser(u)"><Pencil :size="15" /></button>
                  <button v-if="u.username !== me" class="btn icon" title="Delete user" aria-label="Delete user" @click="deleteUser(u)"><Trash2 :size="15" /></button>
                  <span v-else class="btn icon ph" aria-hidden="true"></span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <Modal v-if="editUser" :title="`Edit ${editUser.username}`" @close="editUser = null">
        <form id="user-edit" class="stack" @submit.prevent="saveUser">
          <Field label="Display name"><input v-model="editUser.display_name" type="text"></Field>
          <Field label="New password" hint="Leave empty to keep" :error="errors.password"><input v-model="editUser.password" type="password" autocomplete="new-password"></Field>
          <Field label="Groups"><select v-model="editUser.groups" multiple size="4"><option v-for="g in groups" :key="g.id">{{ g.name }}</option></select></Field>
          <div class="checks">
            <label class="check"><input v-model="editUser.is_admin" type="checkbox" :disabled="editUser.username === me"> Administrator</label>
            <label class="check"><input v-model="editUser.active" type="checkbox" :disabled="editUser.username === me"> Can sign in</label>
          </div>
        </form>
        <template #foot>
          <button type="button" class="btn ghost" @click="editUser = null">Cancel</button>
          <button form="user-edit" class="btn primary">Save user</button>
        </template>
      </Modal>

      <Modal v-if="newUserOpen" title="New user" @close="newUserOpen = false">
        <form id="user-new" class="stack" @submit.prevent="createUser">
          <div class="grid2">
            <Field label="Username" :error="errors.username"><input v-model.trim="nu.username" type="text" class="mono" required autocomplete="off"></Field>
            <Field label="Display name"><input v-model="nu.display_name" type="text"></Field>
          </div>
          <Field label="Password" :error="errors.password" hint="10+ characters; they can change it under their account"><input v-model="nu.password" type="password" autocomplete="new-password" required></Field>
          <Field label="Groups" hint="Roles usually come from groups"><select v-model="nu.groups" multiple size="4"><option v-for="g in groups" :key="g.id">{{ g.name }}</option></select></Field>
          <label class="check"><input v-model="nu.is_admin" type="checkbox"> Administrator (everything, including this page)</label>
        </form>
        <template #foot>
          <button type="button" class="btn ghost" @click="newUserOpen = false">Cancel</button>
          <button form="user-new" class="btn primary">Create user</button>
        </template>
      </Modal>
      <div v-if="sso" class="card ssocard">
        <h3>Single sign-on</h3>
        <dl v-if="sso.oidc.enabled" class="kv small">
          <dt>OpenID Connect</dt><dd>{{ sso.oidc.name }} · <span class="mono">{{ sso.oidc.issuer }}</span></dd>
          <dt>Client</dt><dd class="mono">{{ sso.oidc.client_id }}{{ sso.oidc.confidential ? ' (with a secret)' : ' (public, PKCE)' }}</dd>
          <dt>Redirect URI</dt><dd class="mono">{{ sso.oidc.redirect_uri }}</dd>
          <dt>Accounts</dt><dd>{{ sso.oidc.autocreate ? 'created at the first sign-in' : 'only existing ones (linked by user name the first time)' }};
            groups from the <span class="mono">{{ sso.oidc.groups_claim }}</span> claim<template v-if="sso.oidc.admin_groups">; administrators: <span class="mono">{{ sso.oidc.admin_groups }}</span></template></dd>
          <dt>Password sign-in</dt><dd>{{ sso.oidc.only ? 'hidden (LABDEX_OIDC_ONLY)' : 'still possible' }}</dd>
        </dl>
        <p v-else class="small muted" style="margin: 0">Not set up. Set <code>LABDEX_OIDC_ISSUER</code>, <code>LABDEX_OIDC_CLIENT_ID</code> and
          <code>LABDEX_OIDC_CLIENT_SECRET</code> for sign-in with Authentik, Keycloak, Authelia, Pocket ID …; register
          <span class="mono">{{ sso.oidc.redirect_uri }}</span> as the redirect URI there.</p>
        <p v-if="sso.header" class="small muted" style="margin: 8px 0 0">Also: the SSO proxy header <span class="mono">{{ sso.header }}</span>.</p>
      </div>
    </section>

    <!-- =========================================================== groups -->
    <section v-else-if="tab === 'groups'">
      <div class="sect-head">
        <div><h2>Groups</h2><p class="muted small lead">Bind roles to groups on the Access tab, then add people to groups. With SSO, groups from the proxy's groups header are matched by name.</p></div>
        <span class="grow"></span>
        <button class="btn primary" @click="startGroup(null)"><Plus :size="15" /> Group</button>
      </div>
      <div class="card flush">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Group</th><th>Members</th><th>Roles</th><th></th></tr></thead>
            <tbody>
              <tr v-for="g in groups" :key="g.id">
                <td><b>{{ g.name }}</b><div class="small faint">{{ g.description }}</div></td>
                <td class="small">{{ g.members.join(', ') || '—' }}</td>
                <td class="small"><div v-for="b in rolesOf('group', g.name)" :key="b.id">{{ b.role }} <span class="faint mono">{{ b.scopes.join(' ') }}</span></div></td>
                <td class="right nowrap">
                  <button class="btn icon" title="Edit group" aria-label="Edit group" @click="startGroup(g)"><Pencil :size="15" /></button>
                  <button class="btn icon" title="Delete group" aria-label="Delete group" @click="deleteGroup(g)"><Trash2 :size="15" /></button>
                </td>
              </tr>
              <tr v-if="!groups.length"><td colspan="4" class="empty">No groups yet.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <Modal v-if="editGroup" :title="editGroup.id ? `Edit ${editGroup.name}` : 'New group'" @close="editGroup = null">
        <form id="group-form" class="stack" @submit.prevent="saveGroup">
          <Field label="Name" :error="errors.name"><input v-model.trim="editGroup.name" type="text" required></Field>
          <Field label="Description"><input v-model="editGroup.description" type="text"></Field>
          <Field label="Members"><select v-model="editGroup.members" multiple size="6"><option v-for="u in users" :key="u.id" :value="u.username">{{ u.username }}</option></select></Field>
        </form>
        <template #foot>
          <button type="button" class="btn ghost" @click="editGroup = null">Cancel</button>
          <button form="group-form" class="btn primary">Save group</button>
        </template>
      </Modal>
    </section>

    <!-- ============================================================ roles -->
    <section v-else-if="tab === 'roles'">
      <div class="sect-head">
        <div><h2>Roles</h2><p class="muted small lead">A role is a set of permissions. Where it applies is set when you bind it to someone, on the Access tab.</p></div>
        <span class="grow"></span>
        <button class="btn primary" @click="startRole(null)"><Plus :size="15" /> Role</button>
      </div>
      <div class="card flush">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Role</th><th>Permissions</th><th></th></tr></thead>
            <tbody>
              <tr v-for="r in roles" :key="r.id">
                <td><b>{{ r.name }}</b> <span v-if="r.builtin" class="tag">built in</span><div class="small faint">{{ r.description }}</div></td>
                <td class="mono small">{{ r.permissions.join('  ') }}</td>
                <td class="right nowrap">
                  <button v-if="r.builtin" class="btn icon" title="Copy into a new role" aria-label="Copy role" @click="startRole({ name: `${r.name}-custom`, description: r.description, permissions: r.permissions })"><CopyIcon :size="15" /></button>
                  <button v-else class="btn icon" title="Edit role" aria-label="Edit role" @click="startRole(r)"><Pencil :size="15" /></button>
                  <button v-if="!r.builtin" class="btn icon" title="Delete role" aria-label="Delete role" @click="deleteRole(r)"><Trash2 :size="15" /></button>
                  <span v-else class="btn icon ph" aria-hidden="true"></span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <Modal v-if="editRole" wide :title="editRole.id ? `Edit ${editRole.name}` : 'New role'" @close="editRole = null">
        <form id="role-form" @submit.prevent="saveRole">
          <div class="grid2">
            <Field label="Name" :error="errors.name"><input v-model.trim="editRole.name" type="text" required></Field>
            <Field label="Description"><input v-model="editRole.description" type="text"></Field>
          </div>
          <div class="table-wrap" style="margin-top: 14px">
            <table class="perm">
              <thead><tr><th></th><th v-for="a in actions" :key="a">{{ a }}</th></tr></thead>
              <tbody>
                <tr v-for="t in types" :key="t">
                  <td>{{ t }}</td>
                  <td v-for="a in actions" :key="a"><input type="checkbox" :checked="hasPerm(t, a)" :aria-label="`${t}:${a}`" @change="togglePerm(t, a)"></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="errors.permissions" class="err">{{ errors.permissions }}</div>
          <p class="hint">view → see; edit → change; apply → run integrations and download exports; admin → configure integrations. Edit doesn't imply view: tick both.</p>
        </form>
        <template #foot>
          <button type="button" class="btn ghost" @click="editRole = null">Cancel</button>
          <button form="role-form" class="btn primary">Save role</button>
        </template>
      </Modal>
    </section>

    <!-- =========================================================== access -->
    <div v-else-if="tab === 'access'" class="sections">
      <section>
        <div class="sect-head">
          <div><h2>Role bindings</h2><p class="muted small lead">Who gets which role, and where. Scopes limit a binding to part of the inventory: <code>*</code>, <code>zone:DC_*</code>, <code>area:CA</code>, <code>network:srv</code>, <code>tag:admin</code>, <code>name:srv-*</code>. Globs work.</p></div>
          <span class="grow"></span>
          <button class="btn primary" @click="errors = {}; bindOpen = true"><Plus :size="15" /> Binding</button>
        </div>
        <div class="card flush">
          <div class="table-wrap">
            <table>
              <thead><tr><th>Who</th><th>Role</th><th>Scopes</th><th></th></tr></thead>
              <tbody>
                <tr v-for="b in bindings" :key="b.id">
                  <td>{{ b.user || b.group }} <span class="tag">{{ b.user ? 'user' : 'group' }}</span></td>
                  <td><b>{{ b.role }}</b></td>
                  <td class="mono small">{{ b.scopes.join('  ') }}</td>
                  <td class="right"><button class="btn icon" title="Remove binding" aria-label="Remove binding" @click="removeBinding(b)"><Trash2 :size="15" /></button></td>
                </tr>
                <tr v-if="!bindings.length"><td colspan="4" class="empty">No bindings: only administrators can do anything.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section>
        <div class="sect-head">
          <div><h2>Object grants</h2><p class="muted small lead">Exceptions for single objects, on top of roles: e.g. let one person edit <code>srv-vm-media-*</code>, or run the <code>motd</code> integration.</p></div>
          <span class="grow"></span>
          <button class="btn" @click="errors = {}; grantOpen = true"><Plus :size="15" /> Grant</button>
        </div>
        <div class="card flush">
          <div class="table-wrap">
            <table>
              <thead><tr><th>Who</th><th>Object</th><th>Actions</th><th></th></tr></thead>
              <tbody>
                <tr v-for="g in grants" :key="g.id">
                  <td>{{ g.user || g.group }} <span class="tag">{{ g.user ? 'user' : 'group' }}</span></td>
                  <td><span class="faint">{{ g.object_type }}</span> <span class="mono">{{ g.object_name }}</span></td>
                  <td class="small">{{ g.actions.join(', ') }}</td>
                  <td class="right"><button class="btn icon" title="Remove grant" aria-label="Remove grant" @click="removeGrant(g)"><Trash2 :size="15" /></button></td>
                </tr>
                <tr v-if="!grants.length"><td colspan="4" class="empty">No grants.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <Modal v-if="bindOpen" title="New role binding" @close="bindOpen = false">
        <form id="bind-form" class="stack" @submit.prevent="createBinding">
          <div class="grid2">
            <Field label="Who"><select v-model="nb.who" @change="nb.subject = ''"><option value="group">a group</option><option value="user">a user</option></select></Field>
            <Field :label="nb.who === 'user' ? 'User' : 'Group'" :error="errors.user || errors.group">
              <select v-model="nb.subject" required><option value="" disabled>choose…</option><option v-for="s in subjects(nb.who)" :key="s">{{ s }}</option></select></Field>
          </div>
          <Field label="Role"><select v-model="nb.role"><option v-for="r in roles" :key="r.id">{{ r.name }}</option></select></Field>
          <Field label="Scopes" :error="errors.scopes" hint="* or zone:DC_TC tag:admin, separated by spaces">
            <input v-model="nb.scopes" type="text" class="mono" :class="{ invalid: errors.scopes }" placeholder="*"></Field>
        </form>
        <template #foot>
          <button type="button" class="btn ghost" @click="bindOpen = false">Cancel</button>
          <button form="bind-form" class="btn primary">Add binding</button>
        </template>
      </Modal>

      <Modal v-if="grantOpen" title="New object grant" @close="grantOpen = false">
        <form id="grant-form" class="stack" @submit.prevent="createGrant">
          <div class="grid2">
            <Field label="Who"><select v-model="ng.who" @change="ng.subject = ''"><option value="user">a user</option><option value="group">a group</option></select></Field>
            <Field :label="ng.who === 'user' ? 'User' : 'Group'" :error="errors.user || errors.group">
              <select v-model="ng.subject" required><option value="" disabled>choose…</option><option v-for="s in subjects(ng.who)" :key="s">{{ s }}</option></select></Field>
          </div>
          <div class="grid2">
            <Field label="Object type"><select v-model="ng.object_type"><option v-for="t in types" :key="t">{{ t }}</option></select></Field>
            <Field label="Name" :error="errors.object_name"><input v-model.trim="ng.object_name" type="text" class="mono" placeholder="name or glob" required :class="{ invalid: errors.object_name }"></Field>
          </div>
          <Field label="May" :error="errors.actions">
            <div class="checks row"><label v-for="a in actions" :key="a" class="check tick"><input v-model="ng.actions" type="checkbox" :value="a"> {{ a }}</label></div></Field>
        </form>
        <template #foot>
          <button type="button" class="btn ghost" @click="grantOpen = false">Cancel</button>
          <button form="grant-form" class="btn primary">Add grant</button>
        </template>
      </Modal>
    </div>

    <!-- ====================================================== conventions -->
    <AdminPlugins v-else-if="tab === 'plugins'" />
    <AdminApprovals v-else-if="tab === 'approvals'" />
    <AdminNotifications v-else-if="tab === 'notifications'" />
    <AdminImport v-else-if="tab === 'import'" />

    <div v-else-if="tab === 'general'" class="split">
      <div class="card">
        <h2>General</h2>
        <div class="grid2">
          <Field label="Instance name" :error="errors.instance_name" hint="Shown in the sidebar, e.g. lab.example.net">
            <input v-model.trim="settings.instance_name" type="text">
          </Field>
          <Field label="Hosts count as up for … minutes" :error="errors.live_status_minutes" hint="After the last scan or lease that saw them (Discovery)">
            <input v-model.trim="settings.live_status_minutes" type="number" min="5">
          </Field>
          <Field label="Monitoring reports are fresh for … minutes" :error="errors.status_stale_minutes" hint="Older reports from status plugins count as no data">
            <input v-model.trim="settings.status_stale_minutes" type="number" min="1">
          </Field>
          <Field label="DNS domain" :error="errors.domain" hint="Records become <host>.<network>.<domain>">
            <input v-model.trim="settings.domain" type="text" class="mono">
          </Field>
          <Field label="Firewall id length" :error="errors.alias_max_length" hint="OPNsense allows 32; longer alias names get a shortened id">
            <input v-model.trim="settings.alias_max_length" type="number" min="8" max="64">
          </Field>
          <Field label="Firewall ids" :error="errors.alias_ids" hint="What aliases are called on the firewall; the full labdex name goes into the description. Switching renames them there: sync aliases, then rules; your own rules that use the old names need updating.">
            <select v-model="settings.alias_ids">
              <option value="uid">a short uid: ldx_3fa9c201</option>
              <option value="readable">readable: the name if it fits, else shortened</option>
            </select>
          </Field>
          <Field label="DMZ area code" :error="errors.dmz_area" hint="Hosts here count as internet-facing (red prompt)">
            <input v-model.trim="settings.dmz_area" type="text" class="mono">
          </Field>
          <Field label="Address of this labdex" :error="errors.public_url" hint="For links in notifications">
            <input v-model.trim="settings.public_url" type="text" inputmode="url" class="mono" placeholder="https://labdex.lab.example.net">
          </Field>
        </div>
        <label class="check" style="margin-top: 12px">
          <input type="checkbox" :checked="settings.octet_matches_vlan === 'true'" @change="settings.octet_matches_vlan = $event.target.checked ? 'true' : 'false'">
          Warn when a network's third octet doesn't match its VLAN
        </label>
        <label class="check" style="margin-top: 8px">
          <input type="checkbox" :checked="settings.update_check === 'true'" @change="settings.update_check = $event.target.checked ? 'true' : 'false'">
          Check for new labdex releases (shown on the About page)
        </label>
        <Field label="Project" :error="errors.project_url" style="margin-top: 10px"
               hint="Where labdex lives: GitHub, GitLab or Gitea/Forgejo (Codeberg). The release check asks its public API for the latest release; Help and the top bar link here.">
          <input v-model.trim="settings.project_url" type="url" class="mono" placeholder="https://github.com/labdex-app/labdex">
        </Field>
        <div style="margin-top: 14px"><button class="btn primary" @click="saveSettings">Save</button></div>
      </div>
      <aside class="card">
        <h3>Where things are</h3>
        <ul class="plain small">
          <li><RouterLink to="/zones">Zones</RouterLink>: areas and trust levels (sidebar)</li>
          <li><RouterLink to="/naming">Naming</RouterLink>: hostname parts and fun name pools (sidebar)</li>
          <li><RouterLink to="/about">About</RouterLink>: version, database, plugins, changelog</li>
          <li><RouterLink to="/plugins?tab=market">Plugins → Marketplace</RouterLink> installs plugins; <RouterLink to="/admin/plugins">Settings → Plugins</RouterLink> uploads, repositories and signing keys</li>
          <li><RouterLink to="/plugins">Automation → Plugins</RouterLink>: integrations, their settings, jobs and pages</li>
        </ul>
      </aside>
    </div>

    <!-- =========================================================== backup -->
    <div v-else-if="tab === 'backup'" class="split">
      <div class="card">
        <h2>Restore inventory</h2>
        <p class="muted small" style="margin-top: 0">Replaces networks, hosts, aliases, areas and conventions with a YAML file in one transaction. If anything is invalid, nothing changes. Users, roles, integrations and history stay.</p>
        <input type="file" accept=".yaml,.yml,text/yaml" @change="pickFile">
        <textarea v-model="restoreText" rows="8" placeholder="…or paste YAML here" style="margin-top: 10px"></textarea>
        <div v-if="restoreErrors.length" class="errbox" style="margin-top: 10px">
          <div v-for="(e, n) in restoreErrors.slice(0, 30)" :key="n" class="small"><code>{{ e.field }}</code> {{ e.message }}</div>
          <div v-if="restoreErrors.length > 30" class="small">… and {{ restoreErrors.length - 30 }} more</div>
        </div>
        <button class="btn primary" style="margin-top: 12px" :disabled="busy || !restoreText.trim()" @click="restore">{{ busy ? 'Restoring…' : 'Replace inventory' }}</button>
      </div>
      <aside class="card">
        <h2>Backup</h2>
        <p class="muted small" style="margin-top: 0">Full, scheduled and encrypted backups (database + inventory, to a folder, NFS or S3) are done by the <RouterLink to="/integrations/backup">backup module</RouterLink>. Keep <code>secret.key</code> separately: it isn't in any backup.</p>
        <div class="row">
          <RouterLink class="btn primary" to="/integrations/backup">Backup module</RouterLink>
          <a class="btn" href="/api/admin/backup?download=1">Download inventory.yaml</a>
        </div>
        <p class="hint">The YAML is the format <code>labdex import</code> reads. To keep a copy in git automatically, add the YAML mirror module.</p>
      </aside>
    </div>
    </div>
    </div>
  </div>
</template>

<style scoped>
.adm { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 22px; align-items: start; }
.subnav { position: sticky; top: 16px; display: flex; flex-direction: column; gap: 1px; }
.sg { font-size: calc(12px * var(--text)); color: var(--faint); margin: 14px 10px 4px; font-weight: 500; }
.sg:first-child { margin-top: 0; }
.si { padding: 6px 10px; border-radius: var(--r-ctl); color: var(--muted); font-weight: 500; }
.si:hover { background: var(--raise); color: var(--ink); text-decoration: none; }
.si.router-link-active { background: var(--accent-faint); color: var(--ink); }
@media (max-width: 860px) { .adm { grid-template-columns: minmax(0, 1fr); } .subnav { position: static; flex-direction: row; flex-wrap: wrap; } .sg { display: none; } }
.stack > * + * { margin-top: 12px; }
select[multiple] { padding: 4px; }
.perm { width: auto; }
.lead { margin: 4px 0 0; max-width: 90ch; }
.sections > * + * { margin-top: 28px; }
.ph { visibility: hidden; }
.checks { display: flex; flex-direction: column; gap: 6px; }
.checks.row { flex-direction: row; gap: 16px; }
.right { text-align: right; } .nowrap { white-space: nowrap; }
.perm th, .perm td { padding: 4px 10px; text-align: center; border: 0; background: none; }
.perm td:first-child { text-align: left; color: var(--muted); }
.ssocard { margin-top: 16px; }
.ssocard h3 { margin: 0 0 8px; }
</style>
