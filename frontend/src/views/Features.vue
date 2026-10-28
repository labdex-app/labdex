<script setup>
import { Plus } from 'lucide-vue-next'
import { confirm } from '../confirm'
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, enc } from '../api'
import Field from '../components/Field.vue'
import { stringify as toYaml } from 'yaml'
import { can, state, toast } from '../store'

const list = ref([])
const sel = ref(null) // full feature
const form = ref(null)
const errors = ref({})
const runners = ref([])
const canEdit = computed(() => state.meta.is_admin)

async function load() {
  list.value = await api.get('/api/features')
  if (can('integration', 'view')) runners.value = (await api.get('/api/integrations').catch(() => [])).filter((i) => i.plugin_id === 'ansible')
}
onMounted(() => load().catch((e) => toast(e.message, 'error')))

// tree rows: every defined path plus implied parents, indented by depth
const rows = computed(() => {
  const defined = new Map(list.value.map((f) => [f.path, f]))
  const all = new Set()
  for (const f of list.value) {
    const parts = f.path.split('.')
    parts.forEach((_, i) => all.add(parts.slice(0, i + 1).join('.')))
  }
  return [...all].sort().map((p) => ({ path: p, depth: p.split('.').length - 1, leaf: p.split('.').pop(), f: defined.get(p) }))
})

async function open(path) {
  errors.value = {}
  sel.value = await api.get(`/api/features/${enc(path)}`)
  form.value = { path: sel.value.path, description: sel.value.description, vars: Object.keys(sel.value.vars).length ? toYaml(sel.value.vars) : '',
                 playbooks: sel.value.playbooks.join('\n'), roles: sel.value.roles.join('\n') }
}

function create(parent = '') {
  sel.value = null
  errors.value = {}
  form.value = { path: parent ? `${parent}.` : '', description: '', vars: '', playbooks: '', roles: '' }
}

async function save() {
  errors.value = {}
  try {
    const d = sel.value ? await api.put(`/api/features/${enc(sel.value.path)}`, form.value) : await api.post('/api/features', form.value)
    toast(`Saved ${d.path}`)
    await load()
    await open(d.path)
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
  }
}

async function remove() {
  if (!(await confirm({ title: `Delete ${sel.value.path} and everything below it?`, action: 'Delete', danger: true }))) return
  try {
    await api.del(`/api/features/${enc(sel.value.path)}`)
    toast('Deleted')
    sel.value = form.value = null
    await load()
  } catch (e) { toast(e.message, 'error') }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Features</h1>
      <span class="sub">What runs on a host, as a tree. Children inherit and override their parents' variables.</span>
      <span class="grow"></span>
      <RouterLink v-for="r in runners" :key="r.name" :to="`/integrations/${r.name}`" class="btn small">Runner: {{ r.name }}</RouterLink>
      <button v-if="canEdit" class="btn primary" @click="create()"><Plus :size="15" /> Feature</button>
    </div>

    <div class="split">
      <div class="card flush">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Feature</th><th>Runs</th><th>Hosts</th><th></th></tr></thead>
            <tbody>
              <tr v-for="r in rows" :key="r.path" class="click" :class="{ cur: sel?.path === r.path }" v-rowlink @click="r.f ? open(r.path) : (canEdit && create(r.path.split('.').slice(0, -1).join('.')))">
                <td :style="{ paddingLeft: `${12 + r.depth * 20}px` }">
                  <span class="mono" :class="{ faint: !r.f }">{{ r.depth ? '└ ' : '' }}{{ r.leaf }}</span>
                  <div v-if="r.f?.description" class="small faint" :style="{ paddingLeft: r.depth ? '14px' : 0 }">{{ r.f.description }}</div>
                  <div v-if="!r.f" class="small faint">implied by a child, not defined</div>
                </td>
                <td class="small">{{ r.f ? [...r.f.playbooks, ...r.f.roles.map((x) => `role ${x}`)].join(', ') || '—' : '' }}</td>
                <td class="small">{{ r.f?.hosts || '' }}</td>
                <td class="right"><button v-if="canEdit" class="btn small ghost" title="Add a child" @click.stop="create(r.path)">+</button></td>
              </tr>
              <tr v-if="!rows.length"><td colspan="4" class="empty">No features yet. Start with something like <code>server</code>, then <code>server.packages</code>.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <aside>
        <form v-if="form" class="card" @submit.prevent="save">
          <h2>{{ sel ? sel.path : 'New feature' }}</h2>
          <fieldset class="bare stack" :disabled="!canEdit">
            <Field label="Path" :error="errors.path" hint="Dotted: kube.cluster.oak makes kube and kube.cluster too if they are missing. Renaming moves children and host assignments along.">
              <input v-model.trim="form.path" type="text" class="mono" required>
            </Field>
            <Field label="Description"><input v-model="form.description" type="text"></Field>
            <Field label="Variables (YAML)" :error="errors.vars" hint="Merged into every host below this point, deeper features win. A secret from the vault: password: {$secret: vault:apps/grafana#admin} (filled in only when Ansible runs).">
              <textarea v-model="form.vars" rows="7" spellcheck="false" placeholder="k3s_version: v1.31.2&#10;k3s_cluster: oak"></textarea>
            </Field>
            <Field label="Playbooks" :error="errors.playbooks" hint="One per line, relative to the runner's folder or repository.">
              <textarea v-model="form.playbooks" rows="3" spellcheck="false" placeholder="playbooks/k3s.yml"></textarea>
            </Field>
            <Field label="Roles" :error="errors.roles" hint="From roles/ in the same place; run after the playbooks.">
              <textarea v-model="form.roles" rows="2" spellcheck="false" placeholder="geerlingguy.ntp"></textarea>
            </Field>
          </fieldset>
          <div v-if="canEdit" class="row mt-4">
            <button class="btn primary">Save</button>
            <button v-if="sel" type="button" class="btn danger" @click="remove">Delete</button>
            <button type="button" class="btn ghost" @click="form = sel = null">Close</button>
          </div>
          <p v-else class="hint">Only administrators change features.</p>

          <template v-if="sel">
            <h3 style="margin-top: 18px">Runs in this order</h3>
            <ol class="small chain">
              <li v-for="c in sel.chain" :key="c.path" :class="{ faint: !c.exists }"><span class="mono">{{ c.path }}</span>
                <span class="faint">&nbsp; {{ [...c.playbooks, ...c.roles.map((x) => `role ${x}`)].join(', ') || (c.exists ? 'vars only' : 'not defined') }}</span></li>
            </ol>
            <h3>Merged variables here</h3>
            <pre class="code">{{ Object.keys(sel.merged_vars).length ? toYaml(sel.merged_vars) : '(none)' }}</pre>
            <h3 class="mt-4">Hosts ({{ sel.hosts.length }})</h3>
            <p class="small m-0">
              <RouterLink v-for="(h, n) in sel.hosts" :key="h" :to="`/hosts/${h}`" class="mono">{{ h }}{{ n < sel.hosts.length - 1 ? ', ' : '' }}</RouterLink>
              <span v-if="!sel.hosts.length" class="faint">None yet: add the feature on a host's page.</span>
            </p>
          </template>
        </form>
        <div v-else class="card">
          <h3>How it works</h3>
          <p class="small">A host with <code>kube.cluster.oak</code> runs the playbooks of <code>kube</code>, then <code>kube.cluster</code>, then <code>kube.cluster.oak</code>, with their variables merged in that order and the host's own vars on top. Mappings merge key by key, lists add the items they don't have yet, anything else is replaced; <code>{$replace: [...]}</code> replaces a list or mapping outright.</p>
          <p class="small">The variables also land in the Ansible inventory export, with groups like <code>feature_kube__cluster</code>, so your own playbooks can use them too.</p>
          <p v-if="!runners.length && can('integration', 'admin')" class="small">To run them from here, add the <RouterLink to="/plugins/ansible">Ansible runner</RouterLink>.</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.stack > * + * { margin-top: 12px; }
tr.cur td { background: var(--accent-faint); }
.chain { padding-left: 18px; margin: 0 0 14px; }
</style>
