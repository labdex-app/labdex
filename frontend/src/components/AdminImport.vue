<script setup>
// Add networks and hosts from NetBox, phpIPAM or CSV files. A preview first (nothing is kept),
// then Add. Nothing that exists is changed: those rows are listed as skipped.
import { computed, ref } from 'vue'
import { api, ApiError } from '../api'
import { changed, state, toast } from '../store'
import Field from './Field.vue'

const SOURCES = [{ value: 'csv', label: 'CSV files' }, { value: 'netbox', label: 'NetBox' }, { value: 'phpipam', label: 'phpIPAM' }]
const f = ref({ source: 'csv', zone: '', url: '', token: '', app_id: '', username: '', password: '', verify_tls: true, hosts_text: '', networks_text: '' })
const zones = computed(() => state.meta?.zones || [])
const result = ref(null)
const errors = ref({})
const busy = ref(false)

async function pick(ev, key) { const file = ev.target.files?.[0]; if (file) f.value[key] = await file.text() }

async function run(apply) {
  busy.value = true
  errors.value = {}
  try {
    result.value = await api.post('/api/admin/import/external', { ...f.value, apply })
    if (apply) {
      await changed()
      toast(`Added ${result.value.created.networks.length} networks and ${result.value.created.hosts.length} hosts`)
    }
  } catch (e) {
    result.value = null
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
  } finally { busy.value = false }
}
const added = computed(() => result.value && (result.value.created.networks.length + result.value.created.hosts.length))
</script>

<template>
  <div class="card">
    <h2>Add from other tools</h2>
    <p class="muted small mt-0">Brings networks and hosts from NetBox, phpIPAM or CSV files into labdex. It only adds: what already exists (by name, and networks also by prefix) is left alone, and every new object is in History, so you can revert it. Run a preview first; it keeps nothing.</p>

    <div class="g2">
      <Field label="From"><select v-model="f.source"><option v-for="s in SOURCES" :key="s.value" :value="s.value">{{ s.label }}</option></select></Field>
      <Field label="Zone" :error="errors.zone" hint="Everything comes in under it; sort out the rest on the Hosts and Networks pages afterwards">
        <select v-model="f.zone"><option value="" disabled>choose a zone</option><option v-for="z in zones" :key="z.code" :value="z.code">{{ z.code }}</option></select></Field>
    </div>
    <p v-if="!zones.length" class="small c-yellow">There are no zones yet. Create them under Settings › Zones (or load the example set) first.</p>

    <template v-if="f.source === 'csv'">
      <Field label="Networks file" hint="Columns: name, prefix, vlan, gateway, zone, notes (optional)"><input type="file" accept=".csv,text/csv,text/plain" @change="pick($event, 'networks_text')"></Field>
      <Field label="Hosts file" :error="errors.hosts_text" hint="Columns: name, ip, mac, network, zone, function, status, tags, vendor, model, notes. One address per row; a host on several rows gets several interfaces.">
        <input type="file" accept=".csv,text/csv,text/plain" @change="pick($event, 'hosts_text')"></Field>
    </template>
    <template v-else>
      <Field label="Address" :error="errors.url"><input v-model.trim="f.url" placeholder="https://netbox.example.net"></Field>
      <Field v-if="f.source === 'netbox'" label="API token" :error="errors.token" hint="Read-only is enough"><input v-model="f.token" type="password" autocomplete="off"></Field>
      <div v-else class="g3">
        <Field label="API app id" :error="errors.app_id"><input v-model.trim="f.app_id" placeholder="labdex"></Field>
        <Field label="User" :error="errors.username"><input v-model.trim="f.username" autocomplete="off"></Field>
        <Field label="Password" :error="errors.password"><input v-model="f.password" type="password" autocomplete="off"></Field>
      </div>
      <label class="check small"><input v-model="f.verify_tls" type="checkbox"> Check the server's certificate</label>
    </template>

    <div class="row" style="margin-top: 12px; gap: 8px">
      <button class="btn" :disabled="busy || !f.zone" @click="run(false)">{{ busy ? 'Working …' : 'Preview' }}</button>
      <button class="btn primary" :disabled="busy || !f.zone || !result || result.applied || !added" @click="run(true)">Add {{ added || '' }}</button>
    </div>

    <div v-if="result" class="stack mt-4">
      <p class="small"><b>{{ result.applied ? 'Added' : 'Would add' }}</b>: {{ result.created.networks.length }} networks, {{ result.created.hosts.length }} hosts.
        <span v-if="result.skipped.length">{{ result.skipped.length }} skipped (already there).</span>
        <span v-if="result.failed.length" class="c-red">{{ result.failed.length }} failed.</span></p>
      <div v-if="result.failed.length" class="errbox small"><div v-for="x in result.failed" :key="x.kind + x.name"><b class="mono">{{ x.name }}</b>: {{ x.errors.join('; ') }}</div></div>
      <details v-if="result.warnings.length" open><summary class="small">{{ result.warnings.length }} notes</summary>
        <ul class="small"><li v-for="w in result.warnings.slice(0, 40)" :key="w">{{ w }}</li><li v-if="result.warnings.length > 40">… and {{ result.warnings.length - 40 }} more</li></ul></details>
      <details v-if="result.skipped.length"><summary class="small">Skipped</summary>
        <ul class="small"><li v-for="x in result.skipped.slice(0, 60)" :key="x.kind + x.name"><span class="mono">{{ x.name }}</span>: {{ x.why }}</li></ul></details>
    </div>
  </div>
</template>
