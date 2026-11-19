<script setup>
// Updates: the patch state of every host (updates waiting, security updates, reboots, when it was
// last patched), the picked host's packages beside the list (?peek=<host>). Reports come from
// labdex-patch-report on the hosts and from plugins (patches.py).
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowUpRight } from 'lucide-vue-next'
import Drawer from '../components/Drawer.vue'
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
const route = useRoute()
const router = useRouter()
const open = computed(() => route.query.peek || '')

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
const picked = computed(() => data.value.hosts.find((h) => h.name === open.value) || null)
function toggle(name) {
  const { peek: _, ...rest } = route.query
  router.replace({ query: open.value === name ? rest : { ...rest, peek: name } })
}
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
  <div class="page" :class="{ peeking: picked }">
    <div class="page-head">
      <h1>Updates</h1>
      <span class="h-tag">{{ reported }}/{{ data.hosts.length }} report</span>
      <span class="grow"></span>
      <FilterSelect v-if="data.sites.length > 1" :model-value="site" label="Site" :options="data.sites" @update:model-value="(v) => (site = v || '')" />
      <input v-model="q" type="search" placeholder="Search hosts, OS, kernel" style="max-width: 300px" aria-label="Search hosts">
    </div>

    <div class="kpis">
      <button v-for="c in CARDS" :key="c.k" class="kpi" :class="[c.k, c.tone, { on: state === c.k }]" :aria-pressed="state === c.k" @click="state = state === c.k ? '' : c.k">
        <span class="label">{{ c.label }}</span>
        <b>{{ data.counts[c.k] ?? 0 }}</b>
        <div class="bar"><div :style="{ width: `${pct(data.counts[c.k] ?? 0)}%`, background: BAR[c.tone] }"></div></div>
        <span class="small muted">{{ c.sub(data.counts[c.k] ?? 0) }}</span>
      </button>
    </div>

    <div class="filters">
      <FilterSelect :model-value="state" label="State" :options="STATES.map(([k, v]) => ({ value: k, label: `${v} (${data.counts[k] ?? 0})` }))" @update:model-value="(v) => (state = v || '')" />
    </div>

    <div class="card flush">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Host</th><th>Zone</th><th>State</th><th class="num">Updates</th><th class="num">Security</th><th>OS</th><th>Last patched</th><th>Reported</th></tr></thead>
          <tbody>
            <tr v-for="h in rows" :key="h.name" class="click" :class="{ peek: open === h.name }" v-rowlink :aria-expanded="open === h.name ? 'true' : 'false'"
                @click="toggle(h.name)" @dblclick="router.push(`/hosts/${h.name}?tab=updates`)">
              <td><RouterLink :to="`/hosts/${h.name}`" class="mono nowrap" @click.stop>{{ h.name }}</RouterLink><div v-if="h.fun" class="small faint">{{ h.fun }}</div></td>
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
    </div>
    <p class="hint">
      Hosts report with <a href="/api/patches/agent" download>labdex-patch-report</a> (apt, dnf, Proxmox VE; Python 3, from cron or Ansible),
      plugins report for the systems they manage. A report older than {{ data.stale_days }} days counts as stale.
    </p>

    <Drawer v-if="picked" :key="picked.name" label="Updates · host" :title="picked.name" @close="toggle(picked.name)">
      <div class="facts"><ZoneChip :zone="picked.zone" /><PatchState :state="picked" /><span v-if="picked.os" class="h-tag">{{ picked.os }}</span></div>
      <p v-if="!detail.data.value?.sources?.length" class="small faint">No report for this host yet.</p>
      <section v-for="s in detail.data.value?.sources || []" :key="s.source" class="src pdetail">
        <div class="row small" style="gap: 10px">
          <b class="mono">{{ s.source }}</b>
          <span class="faint">reported {{ ago(s.checked_at) }}</span>
          <span v-if="s.stale" class="pill warn">stale</span>
          <span v-if="s.reboot" class="pill warn">reboot needed</span>
          <span v-if="s.error" class="bad">{{ s.error }}</span>
        </div>
        <table v-if="s.packages.length" class="pk">
          <thead><tr><th>Package</th><th>Installed</th><th>Available</th><th></th></tr></thead>
          <tbody><tr v-for="p in s.packages" :key="p.name">
            <td class="mono">{{ p.name }}</td><td class="mono small faint">{{ p.from || '—' }}</td><td class="mono small">{{ p.to || '—' }}</td>
            <td><span v-if="p.security" class="bad small">security</span></td></tr></tbody>
        </table>
        <p v-else-if="s.pending" class="small faint m-0">{{ s.pending }} updates; this source doesn't list them.</p>
      </section>
      <template #foot>
        <RouterLink :to="`/hosts/${picked.name}?tab=updates`" class="btn">Open host <ArrowUpRight :size="14" /></RouterLink>
      </template>
    </Drawer>
  </div>
</template>

<style scoped>
.num { text-align: right; font-variant-numeric: tabular-nums; }
.bad { color: var(--err); }
tr.click { cursor: pointer; }
tr.peek td { background: var(--accent-faint); }
tr.peek td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
.facts { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.src + .src { margin-top: 16px; }
.pk { margin-top: 8px; width: 100%; }
.pk td, .pk th { padding: 4px 8px; }
td.os { max-width: 220px; }
.pill { font-size: calc(11px * var(--text)); padding: 0 6px; border-radius: var(--r-ctl); }
.pill.warn { color: var(--warn); background: color-mix(in srgb, var(--warn) 14%, transparent); }
@media (min-width: 1200px) { .page.peeking { padding-right: 524px; } }
</style>
