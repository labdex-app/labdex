<script setup>
// Change sets: write a batch of inventory changes as YAML, preview what it would do (hosts, lint,
// firewall aliases, DNS) without saving anything, then apply it as one unit.
import { Check, Play, Plus, Trash2 } from 'lucide-vue-next'
import { ref } from 'vue'
import { api, enc } from '../api'
import { confirm } from '../confirm'
import { changed, toast } from '../store'
import { useQuery } from '../query'

const { data, refresh } = useQuery('/api/changesets', { onError: (e) => toast(e.message, 'error') })
const cur = ref(null) // {uid?, name, description, yaml, status}
const report = ref(null)
const busy = ref(false)

const SAMPLE = `# networks, hosts and aliases are created or updated by name;
# only the keys you give are changed.
networks:
  - {name: guest2, vlan: 232, zone: CA_UT, prefix4: 10.10.232.0/24, gateway4: 10.10.232.254}
hosts:
  - name: cli-lt-ana-01
    zone: CA_UT
    interfaces: [{network: guest2, ip4: 10.10.232.5, primary: true}]
delete:
  hosts: []
`

function fresh() { cur.value = { name: '', description: '', yaml: SAMPLE, status: 'draft' }; report.value = null }
async function open(row) {
  try { cur.value = await api.get(`/api/changesets/${enc(row.uid)}`); report.value = null } catch (e) { toast(e.message, 'error') }
}
async function run(fn) {
  busy.value = true
  try { await fn() } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
const save = () => run(async () => {
  const body = { name: cur.value.name, description: cur.value.description, yaml: cur.value.yaml }
  cur.value = cur.value.uid ? await api.put(`/api/changesets/${enc(cur.value.uid)}`, body) : await api.post('/api/changesets', body)
  await refresh()
  toast('Saved')
})
const preview = () => run(async () => {
  if (!cur.value.uid || cur.value.status === 'draft') await save()
  try { report.value = await api.post(`/api/changesets/${enc(cur.value.uid)}/preview`) } catch (e) { if (e.status === 422) report.value = e.body; else throw e }
})
const apply = () => run(async () => {
  if (!(await confirm({ title: `Apply “${cur.value.name}”?`, body: 'All of it is saved, or none of it. It is checked again now.', action: 'Apply' }))) return
  try {
    report.value = await api.post(`/api/changesets/${enc(cur.value.uid)}/apply`)
    cur.value.status = 'applied'
    await changed()
    await refresh()
    toast('Applied')
  } catch (e) {
    if (e.status === 422 && e.body) report.value = e.body
    else throw e
  }
})
const remove = () => run(async () => {
  if (!(await confirm({ title: `Delete “${cur.value.name}”?`, action: 'Delete', danger: true }))) return
  await api.del(`/api/changesets/${enc(cur.value.uid)}`)
  cur.value = null
  await refresh()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Change sets</h1>
      <span class="sub">A batch of changes, reviewed as one unit: preview the effect on lint, firewall aliases and DNS, then apply all of it or none.</span>
      <span class="grow"></span>
      <button class="btn primary" @click="fresh"><Plus :size="15" /> New change set</button>
    </div>

    <div class="cs-layout">
      <div class="card">
        <p v-if="data && !data.length" class="empty">None yet.</p>
        <ul class="list">
          <li v-for="r in data || []" :key="r.uid">
            <a href="#" @click.prevent="open(r)"><strong>{{ r.name }}</strong></a>
            <span class="tag" :class="r.status === 'applied' ? 'ok' : ''">{{ r.status }}</span>
            <div class="small muted">{{ r.counts.networks }} networks, {{ r.counts.hosts }} hosts, {{ r.counts.aliases }} aliases, {{ r.counts.deletes }} deletes · {{ r.created_by }}</div>
          </li>
        </ul>
      </div>

      <div v-if="cur" class="card">
        <div class="row">
          <input v-model="cur.name" placeholder="Name, e.g. Move guests to VLAN 232" :disabled="cur.status !== 'draft'" aria-label="Name">
        </div>
        <textarea v-model="cur.description" rows="2" placeholder="Why (optional)" :disabled="cur.status !== 'draft'" aria-label="Description"></textarea>
        <textarea v-model="cur.yaml" class="mono" rows="16" spellcheck="false" :disabled="cur.status !== 'draft'" aria-label="Changes (YAML)"></textarea>
        <div v-if="cur.status === 'draft'" class="row">
          <button class="btn" :disabled="busy || !cur.name" @click="save">Save</button>
          <button class="btn" :disabled="busy || !cur.name" @click="preview"><Play :size="14" /> Preview</button>
          <button class="btn primary" :disabled="busy || !cur.uid" @click="apply"><Check :size="14" /> Apply</button>
          <span class="grow"></span>
          <button v-if="cur.uid" class="btn danger" :disabled="busy" @click="remove"><Trash2 :size="14" /> Delete</button>
        </div>
        <p v-else class="small muted">Applied by {{ cur.applied_by }}. The changes are in History under this name.</p>

        <div v-if="report" class="report">
          <h3>{{ report.applied ? 'Applied' : report.ok ? 'Preview: nothing was saved' : 'Not applied: nothing was saved' }}</h3>
          <ul class="list">
            <li v-for="o in report.operations" :key="o.type + o.name">
              <code>{{ o.action || o.op }}</code> {{ o.type }} <strong>{{ o.name }}</strong>
              <span v-if="o.fields.length" class="small muted"> ({{ o.fields.join(', ') }})</span>
              <div v-for="e in o.errors" :key="e" class="small err">{{ e }}</div>
            </li>
          </ul>
          <template v-if="report.lint">
            <p v-if="report.lint.new.length"><strong>New lint findings</strong></p>
            <ul class="list"><li v-for="i in report.lint.new" :key="i.code + i.ref"><span class="tag">{{ i.severity }}</span> {{ i.message }}</li></ul>
            <p v-if="report.lint.resolved.length"><strong>Resolved</strong></p>
            <ul class="list"><li v-for="i in report.lint.resolved" :key="i.code + i.ref">{{ i.message }}</li></ul>
            <p v-if="report.aliases.added.length || report.aliases.removed.length || report.aliases.changed.length" class="small">
              Firewall aliases: +{{ report.aliases.added.length }} / −{{ report.aliases.removed.length }} / ~{{ report.aliases.changed.length }}
            </p>
            <p v-if="report.dns.added.length || report.dns.removed.length" class="small">DNS records: +{{ report.dns.added.length }} / −{{ report.dns.removed.length }}</p>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cs-layout { display: grid; grid-template-columns: minmax(220px, 1fr) 2fr; gap: 1rem; align-items: start; }
@media (max-width: 800px) { .cs-layout { grid-template-columns: 1fr; } }
.cs-layout textarea, .cs-layout input { width: 100%; margin-bottom: .5rem; }
.row { display: flex; gap: .5rem; align-items: center; margin-bottom: .5rem; }
.report { margin-top: 1rem; border-top: 1px solid var(--line); padding-top: .75rem; }
.err { color: var(--danger, #c0392b); }
</style>
