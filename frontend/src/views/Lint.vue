<script setup>
import FilterSelect from '../components/FilterSelect.vue'
import { computed, ref, watch } from 'vue'
import { useQuery } from '../query'
import { api } from '../api'
import { changed, state, toast } from '../store'
import Field from '../components/Field.vue'
import Loading from '../components/Loading.vue'
import Modal from '../components/Modal.vue'

const { data } = useQuery('/api/lint', { onError: (e) => toast(e.message, 'error') })
const sev = ref(null)
const showAccepted = ref(false)
const dlg = ref(null)
const busy = ref(false)
watch(data, (d) => { if (d) state.lint = d.summary })

const acceptedCount = computed(() => (data.value?.issues || []).filter((i) => i.accepted !== undefined).length)
const visible = computed(() => (data.value?.issues || []).filter((i) => (!sev.value || i.severity === sev.value) && (showAccepted.value || i.accepted === undefined)))
const key = (i) => ({ code: i.code, kind: i.kind, ref: i.ref })

async function accept() {
  busy.value = true
  try {
    await api.post('/api/lint/accept', { ...key(dlg.value), reason: dlg.value.reason })
    dlg.value = null
    await changed()
    toast('Accepted')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}

async function unaccept(i) {
  try {
    await api.del('/api/lint/accept', key(i))
    await changed()
    toast('Counts again')
  } catch (e) {
    toast(e.message, 'error')
  }
}
const link = (i) => (i.kind === 'host' ? `/hosts/${i.ref}` : i.kind === 'network' ? `/networks/${i.ref}` : `/aliases`)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Lint</h1>
      <span class="sub">Convention checks. They never block a save; <code>labdex lint</code> exits non-zero on errors for CI.</span>
    </div>

    <template v-if="data">
      <div class="filters">
        <FilterSelect v-model="sev" label="Severity" :options="['error', 'warning', 'info'].map((s) => ({ value: s, label: `${s}s (${data.summary[s]})` }))" />
        <label v-if="acceptedCount" class="small"><input v-model="showAccepted" type="checkbox"> Show accepted ({{ acceptedCount }})</label>
      </div>

      <div v-if="!data.issues.length" class="card empty">
        <div style="font-size: 28px; color: var(--ok)">✓</div>
        Everything follows the conventions.
      </div>
      <div v-else class="card flush">
        <table>
          <thead><tr><th>Severity</th><th>Object</th><th>Check</th><th>Message</th><th v-if="state.meta?.is_admin" /></tr></thead>
          <tbody>
            <tr v-for="(i, n) in visible" :key="n" :class="{ faint: i.accepted !== undefined }">
              <td><span class="sev" :class="`sev-${i.severity}`">{{ i.severity }}</span></td>
              <td class="nowrap"><span class="faint small">{{ i.kind }}</span> <RouterLink :to="link(i)" class="mono">{{ i.ref }}</RouterLink></td>
              <td class="mono small muted">{{ i.code }}</td>
              <td>{{ i.message }}<div v-if="i.accepted !== undefined" class="small muted">Accepted: {{ i.accepted }}</div></td>
              <td v-if="state.meta?.is_admin" class="nowrap">
                <button v-if="i.accepted === undefined" class="btn small" @click="dlg = { ...i, reason: '' }">Accept…</button>
                <button v-else class="btn small" @click="unaccept(i)">Count again</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
    <Loading v-else />

    <Modal v-if="dlg" title="Accept this finding" :busy="busy" @close="dlg = null">
      <p class="small muted">{{ dlg.ref }}: {{ dlg.message }}<br>It stays on this page, but no longer counts in <code>labdex lint</code> or on the dashboard. The reason goes into History.</p>
      <Field label="Why is it fine?"><input v-model.trim="dlg.reason" maxlength="200" placeholder="Legacy printer, stays in the old range until the move"></Field>
      <template #foot>
        <button class="btn" @click="dlg = null">Cancel</button>
        <button class="btn primary" :disabled="!dlg.reason || busy" @click="accept">Accept</button>
      </template>
    </Modal>
  </div>
</template>
