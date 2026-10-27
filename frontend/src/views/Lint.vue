<script setup>
import FilterSelect from '../components/FilterSelect.vue'
import { computed, ref, watch } from 'vue'
import { useQuery } from '../query'
import { state, toast } from '../store'

const { data } = useQuery('/api/lint', { onError: (e) => toast(e.message, 'error') })
const sev = ref(null)
watch(data, (d) => { if (d) state.lint = d.summary })

const visible = computed(() => (data.value?.issues || []).filter((i) => !sev.value || i.severity === sev.value))
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
      </div>

      <div v-if="!data.issues.length" class="card empty">
        <div style="font-size: 28px; color: var(--ok)">✓</div>
        Everything follows the conventions.
      </div>
      <div v-else class="card flush">
        <table>
          <thead><tr><th>Severity</th><th>Object</th><th>Check</th><th>Message</th></tr></thead>
          <tbody>
            <tr v-for="(i, n) in visible" :key="n">
              <td><span class="sev" :class="`sev-${i.severity}`">{{ i.severity }}</span></td>
              <td class="nowrap"><span class="faint small">{{ i.kind }}</span> <RouterLink :to="link(i)" class="mono">{{ i.ref }}</RouterLink></td>
              <td class="mono small muted">{{ i.code }}</td>
              <td>{{ i.message }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
