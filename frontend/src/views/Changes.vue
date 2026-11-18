<script setup>
// Changes: every plan, with the selected one beside the list (Plan, Review, Apply, Verify).
// Plans come from integrations (git sync, DNS, firewall, Pulumi, patching …); approving and
// applying go through the job's own routes, so approval policies and change windows hold.
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { GitPullRequestArrow } from 'lucide-vue-next'
import JobDetail from '../components/JobDetail.vue'
import { useQuery } from '../query'
import { ago, state, toast } from '../store'

const route = useRoute()
const router = useRouter()
const { data, refresh } = useQuery('/api/jobs?limit=100', { staleMs: 5000, onError: (e) => toast(e.message, 'error') })
watch(() => state.pending, () => refresh())

const OPEN = ['queued', 'running', 'planned', 'approved', 'applying']
const FILTERS = [
  { key: 'open', label: 'Open', test: (j) => OPEN.includes(j.status) },
  { key: 'applied', label: 'Applied', test: (j) => ['applied', 'succeeded'].includes(j.status) },
  { key: 'failed', label: 'Failed', test: (j) => j.status === 'failed' },
  { key: 'all', label: 'All', test: () => true },
]
const filter = computed(() => FILTERS.find((f) => f.key === route.query.show) || FILTERS[0])
const plans = computed(() => (data.value || []).filter((j) => j.mode === 'plan' && (!state.site || !j.site || j.site === state.site)))
const count = (f) => plans.value.filter(f.test).length
const rows = computed(() => plans.value.filter(filter.value.test))
const selected = computed(() => route.query.job || rows.value[0]?.id || null)

function show(key) { router.replace({ query: { ...route.query, show: key === 'open' ? undefined : key, job: undefined } }) }
function pick(id) { router.replace({ query: { ...route.query, job: String(id) } }); refresh() }

const DOT = { planned: 'pending', approved: 'pending', queued: 'info', running: 'info', applying: 'info', applied: 'ok', succeeded: 'ok', failed: 'err' }
function sub(j) {
  const o = j.ops || {}
  const parts = [o.create && `${o.create} to add`, o.update && `${o.update} to change`, o.delete && `${o.delete} to remove`].filter(Boolean)
  if (j.status === 'succeeded' && !j.changes) return 'in sync, nothing to change'
  if (j.status === 'applied') return `${j.changes} applied${j.applied_by ? ` by ${j.applied_by}` : ''}`
  if (j.status === 'failed') return 'failed'
  if (j.status === 'stale') return 'out of date'
  if (j.status === 'discarded') return 'discarded'
  return parts.join(', ') || j.status
}
</script>

<template>
  <div class="page ld">
    <section class="card flush list-pane" aria-label="Plans">
      <div class="lp-head">
        <h1>Changes</h1>
      </div>
      <div class="filters lp-filters" role="group" aria-label="Show">
        <button v-for="f in FILTERS" :key="f.key" class="chip" :class="{ on: filter.key === f.key }" :aria-pressed="filter.key === f.key" @click="show(f.key)">
          {{ f.label }}<span v-if="f.key !== 'all'" class="n">{{ count(f) }}</span></button>
      </div>
      <div v-if="!data" class="lp-empty faint">Loading…</div>
      <div v-else-if="!rows.length" class="lp-empty">
        <GitPullRequestArrow :size="22" class="faint" />
        <p class="m-0"><b>{{ filter.key === 'open' ? 'Nothing waiting' : 'No plans here' }}</b></p>
        <p class="small muted m-0">Integrations plan here when the inventory or git changes, or when you run an action.</p>
      </div>
      <ul v-else class="rows">
        <li v-for="j in rows" :key="j.id">
          <button class="row-btn" :class="{ on: String(selected) === String(j.id) }" :aria-current="String(selected) === String(j.id) ? 'true' : undefined" @click="pick(j.id)">
            <span class="dot" :class="DOT[j.status] || 'unknown'" aria-hidden="true"></span>
            <span class="rb-main">
              <span class="rb-title">{{ j.action }}<template v-if="j.hosts?.length === 1">: {{ j.hosts[0] }}</template></span>
              <span class="small muted">{{ j.integration }}<template v-if="j.site && !state.site"> · {{ j.site }}</template> · {{ sub(j) }}</span>
            </span>
            <span class="rb-side"><span>#{{ j.id }}</span><span>{{ ago(j.created_at).replace(' ago', '') }}</span></span>
          </button>
        </li>
      </ul>
    </section>
    <section class="card detail-pane" aria-label="The selected plan">
      <JobDetail v-if="selected" :id="selected" :key="selected" embedded @go="pick" @changed="refresh" />
      <p v-else class="empty">Pick a plan on the left.</p>
    </section>
  </div>
</template>

<style scoped>
.ld { display: grid; grid-template-columns: minmax(280px, 360px) minmax(0, 1fr); gap: 16px; align-items: start; max-width: 1480px; }
.list-pane { position: sticky; top: 66px; max-height: calc(100vh - 110px); display: flex; flex-direction: column; }
.lp-head { display: flex; align-items: center; gap: 8px; padding: 14px 16px 10px; }
.lp-head h1 { font-size: calc(16px * var(--text)); }
.lp-filters { padding: 0 16px 12px; margin: 0; border-bottom: 1px solid var(--line); }
.chip .n { margin-left: 5px; font-family: var(--mono); font-size: .9em; color: var(--faint); }
.lp-empty { padding: 28px 18px; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.rows { list-style: none; margin: 0; padding: 0; overflow-y: auto; }
.rows li + li { border-top: 1px solid var(--line); }
.row-btn { display: flex; align-items: flex-start; gap: 10px; width: 100%; text-align: left; font: inherit; color: var(--ink); background: none;
  border: 0; border-left: 3px solid transparent; padding: 12px 14px 12px 13px; cursor: pointer; }
.row-btn:hover { background: var(--hover); }
.row-btn.on { background: var(--accent-faint); border-left-color: var(--accent); }
.row-btn .dot { margin-top: 7px; }
.rb-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.rb-title { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rb-side { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; font: calc(11.5px * var(--text)) var(--mono); color: var(--faint); white-space: nowrap; }
.detail-pane { padding: 22px 24px; min-height: 320px; margin-top: 0 !important; }
@media (max-width: 1000px) {
  .ld { grid-template-columns: minmax(0, 1fr); }
  .list-pane { position: static; max-height: 46vh; }
}
</style>
