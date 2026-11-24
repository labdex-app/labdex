<script setup>
// Updates: the patch state of every host (updates waiting, security updates, reboots, when it was
// last patched); a click opens the host's Updates tab with its packages. Reports come from
// labdex-patch-report on the hosts and from plugins (patches.py).
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { PackageCheck } from 'lucide-vue-next'
import FilterSelect from '../components/FilterSelect.vue'
import PatchState from '../components/PatchState.vue'
import ZoneChip from '../components/ZoneChip.vue'
import { useQuery } from '../query'
import { ago, stamp, toast } from '../store'
import Pager from '../components/Pager.vue'
import { usePaged } from '../paged'

const site = ref('')
const query = useQuery(() => `/api/patches${site.value ? `?site=${encodeURIComponent(site.value)}` : ''}`, { onError: (e) => toast(e.message, 'error') })
const data = computed(() => query.data.value || { hosts: [], counts: {}, sites: [], stale_days: 3 })
const q = ref('')
const state = ref('')
const router = useRouter()

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
const { page, rows: paged } = usePaged(rows, { resetOn: [q, site, state] })
const reported = computed(() => data.value.hosts.filter((h) => h.state !== 'unknown').length)
// hosts have no side panel: their packages are on the host page's Updates tab
const openHost = (name) => router.push(`/hosts/${encodeURIComponent(name)}?tab=updates`)
// the cards: what they count, how it reads, the colour of the number
const CARDS = [
  { k: 'security', label: 'Security updates', tone: 'err', sub: (n) => `${n} host${n === 1 ? '' : 's'} with security fixes` },
  { k: 'reboot', label: 'Reboot needed', tone: 'warn', sub: (n) => (n ? 'after their last updates' : 'none waiting') },
  { k: 'pending', label: 'Updates waiting', tone: '', sub: () => 'no security fixes among them' },
  { k: 'current', label: 'Up to date', tone: 'ok', sub: () => `of ${data.value.hosts.length} hosts` },
]
const pct = (n) => (data.value.hosts.length ? Math.round((100 * n) / data.value.hosts.length) : 0)
const BAR = { err: 'var(--err)', warn: 'var(--warn)', ok: 'var(--ok)', '': 'var(--accent)' }
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Updates</h1>
      <span class="h-tag">{{ reported }}/{{ data.hosts.length }} report</span>
      <span class="grow"></span>
      <FilterSelect v-if="data.sites.length > 1" :model-value="site" label="Site" :options="data.sites" @update:model-value="(v) => (site = v || '')" />
      <input v-model="q" type="search" placeholder="Search hosts, OS, kernel" aria-label="Search hosts">
    </div>

    <div class="kpis">
      <button v-for="c in CARDS" :key="c.k" class="kpi" :class="[c.k, c.tone && `t-${c.tone}`, { on: state === c.k }]" :aria-pressed="state === c.k" @click="state = state === c.k ? '' : c.k">
        <span class="label">{{ c.label }}</span>
        <b>{{ data.counts[c.k] ?? 0 }}</b>
        <div class="bar"><div :style="{ width: `${pct(data.counts[c.k] ?? 0)}%`, background: BAR[c.tone] }"></div></div>
        <span class="small muted">{{ c.sub(data.counts[c.k] ?? 0) }}</span>
      </button>
    </div>

    <div v-if="query.data.value && data.hosts.length && !reported" class="card empty mb-4">
      <div class="e-ic"><PackageCheck :size="18" /></div>
      <div class="e-t">No host reports its updates yet</div>
      <p>Link an agent, run <span class="mono">labdex-patch-report</span> from cron or Ansible, or add a plugin that reports them.</p>
      <div class="e-acts"><a href="/api/patches/agent" download class="btn">Download labdex-patch-report</a><RouterLink to="/agents" class="btn ghost">Agents</RouterLink></div>
    </div>

    <div class="filters">
      <FilterSelect :model-value="state" label="State" :options="STATES.map(([k, v]) => ({ value: k, label: `${v} (${data.counts[k] ?? 0})` }))" @update:model-value="(v) => (state = v || '')" />
    </div>

    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Host</th><th>Zone</th><th>State</th><th class="num">Updates</th><th class="num">Security</th><th>OS</th><th>Last patched</th><th>Reported</th></tr></thead>
          <tbody>
            <tr v-for="h in paged" :key="h.name" class="click" v-rowlink @click="openHost(h.name)">
              <td><RouterLink :to="`/hosts/${h.name}?tab=updates`" class="mono nowrap" @click.stop>{{ h.name }}</RouterLink><div v-if="h.fun" class="small faint">{{ h.fun }}</div></td>
              <td><ZoneChip :zone="h.zone" /></td>
              <td><PatchState :state="h" /></td>
              <td class="num mono">{{ h.state === 'unknown' ? '—' : h.pending }}</td>
              <td class="num mono" :class="{ bad: h.security }">{{ h.state === 'unknown' ? '—' : h.security }}</td>
              <td class="small os" :title="h.kernel ? `kernel ${h.kernel}` : ''">{{ h.os || '—' }}</td>
              <td class="small" :title="stamp(h.patched_at)">{{ ago(h.patched_at) }}</td>
              <td class="small" :title="stamp(h.checked_at)">{{ ago(h.checked_at) }}<div v-if="h.sources.length" class="small faint">{{ h.sources.join(', ') }}</div></td>
            </tr>
            <tr v-if="!rows.length"><td colspan="8" class="empty">{{ data.hosts.length ? 'Nothing matches.' : 'No hosts yet.' }}</td></tr>
          </tbody>
        </table>
      </div>
      <Pager v-model="page" :total="rows.length" unit="hosts" />
    </div>
    <p class="hint">
      Hosts report with <a href="/api/patches/agent" download>labdex-patch-report</a> (apt, dnf, Proxmox VE; Python 3, from cron or Ansible),
      plugins report for the systems they manage. A report older than {{ data.stale_days }} days counts as stale.
    </p>

  </div>
</template>

<style scoped>
.num { text-align: right; font-variant-numeric: tabular-nums; }
.bad { color: var(--err); }
tr.click { cursor: pointer; }
td.os { max-width: 220px; }
</style>
