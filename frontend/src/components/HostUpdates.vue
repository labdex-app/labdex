<script setup>
// What a host's patch reports say is waiting, package by package, from the freshest report.
// Planning the run happens on the host's header (Plan patch run) and is approved on Changes.
import { computed } from 'vue'
import PatchState from './PatchState.vue'
import { ago, stamp } from '../store'

const props = defineProps({ host: { type: Object, required: true } })
const p = computed(() => props.host.patches || { state: 'unknown', sources: [] })
const latest = computed(() => [...(p.value.sources || [])].sort((a, b) => String(b.checked_at).localeCompare(String(a.checked_at)))[0] || null)
const pkgs = computed(() => [...(latest.value?.packages || [])].sort((a, b) => Number(!!b.security) - Number(!!a.security) || String(a.name).localeCompare(String(b.name))))
</script>

<template>
  <section class="card flush">
    <div class="sect-head pad"><h2>Updates</h2><PatchState :state="p" /><span class="grow"></span>
      <span v-if="latest" class="small faint" :title="stamp(latest.checked_at)">reported {{ ago(latest.checked_at) }} by {{ latest.source }}</span></div>
    <p v-if="p.state === 'unknown'" class="empty-line pad">Nothing reports updates for this host. Link its agent, or see <RouterLink to="/patches">Updates</RouterLink> for other ways.</p>
    <p v-else-if="!pkgs.length" class="empty-line pad">{{ p.pending ? `${p.pending} update${p.pending === 1 ? '' : 's'} waiting; the report doesn't list the packages.` : 'Up to date.' }}
      <template v-if="p.reboot"> A reboot is needed.</template></p>
    <div v-else class="table-wrap">
      <table>
        <thead><tr><th>Package</th><th>Installed</th><th>Available</th><th></th></tr></thead>
        <tbody>
          <tr v-for="k in pkgs" :key="k.name">
            <td class="mono">{{ k.name }}</td><td class="mono small faint">{{ k.from || '—' }}</td><td class="mono small">{{ k.to || '—' }}</td>
            <td><span v-if="k.security" class="sev sev-error">security</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="p.reboot && pkgs.length" class="hint pad">A reboot is needed after these.</p>
  </section>
</template>

<style scoped>
.pad { padding: 14px 18px; margin: 0; }
.sect-head { display: flex; align-items: center; gap: 10px; }
</style>
