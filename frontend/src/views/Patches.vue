<script setup>
// Patch state of every host: updates waiting, security updates, reboots, when it was last patched.
// Reports come from labdex-patch-report on the hosts and from plugins (patches.py).
import { computed, ref } from 'vue'
import FilterSelect from '../components/FilterSelect.vue'
import PatchState from '../components/PatchState.vue'
import ZoneChip from '../components/ZoneChip.vue'
import { useQuery } from '../query'
import { ago, stamp, toast } from '../store'

const site = ref('')
const query = useQuery(() => `/api/patches${site.value ? `?site=${encodeURIComponent(site.value)}` : ''}`, { onError: (e) => toast(e.message, 'error') })
const data = computed(() => query.data.value || { hosts: [], counts: {}, sites: [], stale_days: 3 })
const q = ref('')
const state = ref('')
const open = ref('')

const STATES = [
  ['security', 'security updates'], ['reboot', 'reboot needed'], ['pending', 'updates waiting'], ['current', 'up to date'],
  ['stale', 'stale'], ['error', 'error'], ['unknown', 'no report'],
]
const ORDER = Object.fromEntries(STATES.map(([k], i) => [k, i]))
const rows = computed(() => {
  const n = q.value.trim().toLowerCase()
  return data.value.hosts
    .filter((h) => (!state.value || h.state === state.value || (state.value === 'reboot' && h.reboot)) &&
      (!n || [h.name, h.fun, h.os, h.kernel, h.zone].join(' ').toLowerCase().includes(n)))
    .sort((a, b) => ORDER[a.state] - ORDER[b.state] || b.security - a.security || b.pending - a.pending || a.name.localeCompare(b.name))
})
const reported = computed(() => data.value.hosts.filter((h) => h.state !== 'unknown').length)
const detail = useQuery(() => (open.value ? `/api/hosts/${open.value}/patches` : null))
function toggle(name) { open.value = open.value === name ? '' : name }
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Patches</h1>
      <span class="sub">{{ reported }} of {{ data.hosts.length }} hosts report</span>
      <span class="grow"></span>
      <input v-model="q" type="search" placeholder="Search hosts, OS, kernel" style="max-width: 300px" aria-label="Search hosts">
    </div>

    <div class="tiles">
      <button v-for="[k, label] in STATES.slice(0, 4)" :key="k" class="tile" :class="[k, { on: state === k }]" @click="state = state === k ? '' : k">
        <b>{{ data.counts[k] ?? 0 }}</b><span>{{ label }}</span>
      </button>
    </div>

    <div class="filters">
      <FilterSelect :model-value="state" label="State" :options="STATES.map(([k, v]) => ({ value: k, label: `${v} (${data.counts[k] ?? 0})` }))" @update:model-value="(v) => (state = v || '')" />
      <FilterSelect v-if="data.sites.length > 1" :model-value="site" label="Site" :options="data.sites" @update:model-value="(v) => (site = v || '')" />
    </div>

    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Host</th><th>Zone</th><th>State</th><th class="num">Updates</th><th class="num">Security</th><th>OS</th><th>Last patched</th><th>Reported</th></tr></thead>
          <tbody>
            <template v-for="h in rows" :key="h.name">
              <tr class="click" :class="{ sel: open === h.name }" @click="toggle(h.name)">
                <td><RouterLink :to="`/hosts/${h.name}`" class="mono" @click.stop>{{ h.name }}</RouterLink><div v-if="h.fun" class="small faint">{{ h.fun }}</div></td>
                <td><ZoneChip :zone="h.zone" /></td>
                <td><PatchState :state="h" /></td>
                <td class="num">{{ h.state === 'unknown' ? '—' : h.pending }}</td>
                <td class="num" :class="{ bad: h.security }">{{ h.state === 'unknown' ? '—' : h.security }}</td>
                <td class="small os" :title="h.kernel ? `kernel ${h.kernel}` : ''">{{ h.os || '—' }}</td>
                <td class="small" :title="stamp(h.patched_at)">{{ ago(h.patched_at) }}</td>
                <td class="small" :title="stamp(h.checked_at)">{{ ago(h.checked_at) }}<div v-if="h.sources.length" class="small faint">{{ h.sources.join(', ') }}</div></td>
              </tr>
              <tr v-if="open === h.name" class="pdetail">
                <td colspan="8">
                  <p v-if="!detail.data.value?.sources?.length" class="small faint m-0">No report for this host yet.</p>
                  <div v-for="s in detail.data.value?.sources || []" :key="s.source" class="src">
                    <div class="row small" style="gap: 10px">
                      <b class="mono">{{ s.source }}</b>
                      <span class="faint">reported {{ ago(s.checked_at) }}</span>
                      <span v-if="s.stale" class="pill warn">stale</span>
                      <span v-if="s.reboot" class="pill warn">reboot needed</span>
                      <span v-if="s.error" class="bad">{{ s.error }}</span>
                    </div>
                    <table v-if="s.packages.length" class="pk">
                      <tbody><tr v-for="p in s.packages" :key="p.name">
                        <td class="mono">{{ p.name }}</td><td class="mono small faint">{{ p.from }}</td><td class="mono small">{{ p.to }}</td>
                        <td><span v-if="p.security" class="bad small">security</span></td></tr></tbody>
                    </table>
                    <p v-else-if="s.pending" class="small faint m-0">{{ s.pending }} updates; this source doesn't list them.</p>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="!rows.length"><td colspan="8" class="empty">{{ data.hosts.length ? 'Nothing matches.' : 'No hosts.' }}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <p class="hint">
      Hosts report with <a href="/api/patches/agent" download>labdex-patch-report</a> (apt, dnf, Proxmox VE; Python 3, from cron or Ansible),
      plugins report for the systems they manage. A report older than {{ data.stale_days }} days counts as stale.
    </p>
  </div>
</template>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px; }
.tile { font: inherit; text-align: left; background: var(--card); border: 1px solid var(--line); border-radius: var(--r-box); padding: 10px 14px;
  display: flex; flex-direction: column; gap: 2px; cursor: pointer; color: var(--muted); }
.tile b { font-size: calc(22px * var(--text)); color: var(--ink); }
.tile.security b { color: var(--err); }
.tile.reboot b { color: var(--warn); }
.tile.current b { color: var(--ok); }
.tile.on { border-color: var(--accent); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.bad { color: var(--err); }
tr.click { cursor: pointer; }
tr.sel td { background: var(--raise); }
tr.pdetail td { background: var(--raise); padding: 10px 14px; }
.src + .src { margin-top: 12px; }
.pk { margin-top: 6px; }
.pk { width: auto; }
tr.pdetail .pk td { padding: 2px 8px; border: 0; white-space: nowrap; }
td.os { max-width: 220px; }
.pill { font-size: calc(11px * var(--text)); padding: 0 6px; border-radius: var(--r-ctl); }
.pill.warn { color: var(--warn); background: color-mix(in srgb, var(--warn) 14%, transparent); }
</style>
