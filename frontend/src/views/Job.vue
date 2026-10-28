<script setup>
import { confirm } from '../confirm'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import JobBadge from '../components/JobBadge.vue'
import Diff from '../components/Diff.vue'
import { stamp, toast, unwatchJob, watchJob } from '../store'

const props = defineProps({ id: String })
const j = ref(null)
const open = ref(new Set())
const busy = ref(false)
const logEl = ref(null)
const follow = ref(true)

const RUNNING = ['queued', 'running', 'applying']
const queue = ref(null)
const waiting = computed(() => j.value && !j.value.worker && ['queued', 'applying'].includes(j.value.status))
async function load() {
  try {
    j.value = await api.get(`/api/jobs/${props.id}`)
    if (waiting.value) queue.value = await api.get('/api/jobs/queue').catch(() => null)
  } catch (e) {
    toast(e.message, 'error')
  }
}

function scrollLog() {
  if (follow.value && logEl.value) nextTick(() => { logEl.value.scrollTop = logEl.value.scrollHeight })
}

// Live: status and new log lines arrive over the event stream; no polling.
onMounted(async () => {
  await load()
  let first = true
  watchJob(Number(props.id), (d) => {
    if (!j.value) return
    const log = j.value.log
    Object.assign(j.value, d, { log })
    if (!first && !RUNNING.includes(d.status)) load() // final plan and result
    first = false
  }, (text) => {
    if (!j.value) return
    // the stream starts from the beginning; keep what we have if it's longer
    if (!j.value._streamed) { j.value.log = ''; j.value._streamed = true }
    j.value.log += text
    scrollLog()
  })
})
onBeforeUnmount(() => unwatchJob(Number(props.id)))

const comment = ref('')
const lastApproval = computed(() => j.value && (j.value.approvals || []).length + 1 >= (j.value.needed ?? 1))
const OUTCOME = { scheduled: 'Approved; it applies when its time comes', waiting: 'Approved; more approvals needed', replanned: 'The inventory changed: planned again' }
async function act(what) {
  if (what === 'approve' && lastApproval.value && !j.value.policy_detail?.window && !j.value.policy_detail?.delay_minutes && !(await confirm({ title: `Approve and apply ${kept.value} change(s) with ${j.value.integration}?`, body: skip.value.size ? `${skip.value.size} unticked change(s) are left out.` : '', action: 'Approve and apply' }))) return
  if (what === 'discard' && !(await confirm({ title: 'Discard this plan?', action: 'Discard', danger: true }))) return
  busy.value = true
  try {
    const body = { comment: comment.value }
    if (what === 'approve' && canPick.value) body.skip = [...skip.value]
    const d = await api.post(`/api/jobs/${props.id}/${what}`, body)
    comment.value = ''
    if (OUTCOME[d.outcome]) toast(OUTCOME[d.outcome])
    Object.assign(j.value, d)
  } catch (e) {
    toast(e.message, 'error')
    await load()
  } finally {
    busy.value = false
  }
}

function toggle(n) {
  const s = new Set(open.value)
  s.has(n) ? s.delete(n) : s.add(n)
  open.value = s
}

const counts = computed(() => {
  const c = {}
  for (const x of j.value?.plan || []) c[x.op] = (c[x.op] || 0) + 1
  return c
})
const fmt = (v) => (v == null ? '' : typeof v === 'string' ? v : JSON.stringify(v, null, 2))

// ------------------------------------------------ reading a plan: grouped, field by field
// Flatten {a: {b: 1}, c: [x]} to {"a.b": 1, "c[0]": x}, so an update shows only what differs.
function flat(v, pre = '', out = {}) {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const keys = Object.keys(v)
    if (!keys.length && pre) out[pre] = '{}'
    for (const k of keys) flat(v[k], pre ? `${pre}.${k}` : k, out)
  } else if (Array.isArray(v) && v.length && v.every((x) => x && typeof x === 'object')) {
    v.forEach((x, i) => flat(x, `${pre}[${i}]`, out))
  } else out[pre || 'value'] = Array.isArray(v) ? v.join(', ') : v
  return out
}
const structured = (c) => (c.before && typeof c.before === 'object') || (c.after && typeof c.after === 'object')
function fields(c) {
  const a = flat(c.before ?? {}), b = flat(c.after ?? {})
  return [...new Set([...Object.keys(a), ...Object.keys(b)])]
    .filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]))
    .map((k) => ({ path: k, before: a[k], after: b[k] }))
}
const grouped = computed(() => {
  const out = []
  ;(j.value?.plan || []).forEach((c, n) => {
    let g = out.find((x) => x.kind === c.kind)
    if (!g) out.push(g = { kind: c.kind, items: [] })
    g.items.push({ ...c, n })
  })
  return out
})
// unticking changes: only while you can still approve, and not for an Ansible run
const skip = ref(new Set())
watch(() => j.value?.plan, (plan) => { skip.value = new Set((plan || []).map((c, n) => (c.skipped ? n : -1)).filter((n) => n >= 0)) }, { immediate: true })
const canPick = computed(() => j.value?.status === 'planned' && j.value?.can_approve && (j.value.plan || []).length > 1 && !(j.value.plan || []).some((c) => c.op === 'ansible'))
function flip(n) { const s = new Set(skip.value); s.has(n) ? s.delete(n) : s.add(n); skip.value = s }
function flipGroup(g) {
  const s = new Set(skip.value)
  const allOff = g.items.every((c) => s.has(c.n))
  for (const c of g.items) allOff ? s.delete(c.n) : s.add(c.n)
  skip.value = s
}
const kept = computed(() => (j.value?.plan || []).length - skip.value.size)
const resultText = computed(() => {
  const r = j.value?.result
  if (!r) return ''
  if (r.error) return ''
  return r.message || Object.entries(r).filter(([k]) => !['discarded_by', 'comment', 'replanned_as'].includes(k)).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')
})
</script>

<template>
  <div class="page">
    <template v-if="j">
      <div class="page-head">
        <RouterLink :to="`/integrations/${j.integration}`" class="btn ghost small">← {{ j.integration }}</RouterLink>
        <h1>{{ j.action }}</h1>
        <RouterLink v-if="j.params?.host" :to="`/hosts/${j.params.host}`" class="mono">{{ j.params.host }}</RouterLink>
        <span class="sub">job #{{ j.id }}</span>
        <JobBadge :status="j.status" />
        <span class="grow"></span>
        <template v-if="['planned', 'approved'].includes(j.status) && j.can_apply">
          <button class="btn" :disabled="busy" @click="act('discard')">Discard</button>
          <button v-if="j.can_approve" class="btn primary" :disabled="busy" @click="act('approve')">
            <template v-if="!lastApproval">Approve ({{ (j.approvals || []).length + 1 }} of {{ j.needed }})</template>
            <template v-else-if="j.policy_detail?.window || j.policy_detail?.delay_minutes">Approve {{ kept }} change{{ kept === 1 ? '' : 's' }}</template>
            <template v-else>Approve &amp; apply {{ kept }} change{{ kept === 1 ? '' : 's' }}</template>
          </button>
        </template>
      </div>

      <div class="row small muted" style="margin: -8px 0 16px">
        <span>{{ j.trigger === 'hook' ? 'Started by an inventory change' : j.trigger === 'schedule' ? 'Scheduled' : `Started by ${j.created_by}` }} · {{ stamp(j.created_at) }}</span>
        <span v-if="j.applied_by">· applied by {{ j.applied_by }}</span>
        <span v-if="j.finished_at">· finished {{ stamp(j.finished_at) }}</span>
        <span v-if="j.worker" class="faint" :title="`worker ${j.worker}`">· on {{ j.worker }}</span>
      </div>

      <div v-if="Object.entries(j.params || {}).filter(([k, v]) => k !== 'host' && v !== '' && v != null).length" class="small muted" style="margin: -8px 0 16px">
        Parameters: <code v-for="(v, k) in j.params" v-show="k !== 'host' && v !== '' && v != null" :key="k" style="margin-right: 10px">{{ k }}={{ Array.isArray(v) ? v.join(',') : v }}</code>
      </div>
      <div v-if="j.result?.error" class="errbox" style="margin-bottom: 16px"><b>Failed:</b> {{ j.result.error }}</div>
      <div v-else-if="resultText" class="card" style="margin-bottom: 16px">{{ resultText }}</div>
      <div v-if="waiting && queue && !queue.ok" class="warnbox" style="margin-bottom: 16px">
        Waiting for a worker: jobs are left to <code>labdex worker</code> (LABDEX_WORKER=external), and none was seen in the last minute.
      </div>
      <div v-if="j.status === 'stale'" class="warnbox" style="margin-bottom: 16px">
        This plan is out of date: the inventory changed or a newer plan replaced it.
        <RouterLink v-if="j.replanned_as" :to="`/jobs/${j.replanned_as}`">See the new plan (job #{{ j.replanned_as }})</RouterLink>
        <template v-else>Run the action again for a fresh plan.</template>
      </div>

      <div v-if="j.mode === 'plan' && j.policy" class="card approvals" style="margin-bottom: 16px">
        <div class="row">
          <h2 style="margin: 0">Approvals</h2>
          <span class="small muted">policy <b>{{ j.policy }}</b> ·
            <template v-if="j.needed === 0">applies without approval</template>
            <template v-else>{{ (j.approvals || []).length }} of {{ j.needed }}</template>
            <template v-if="j.policy_detail?.approvers?.length"> from {{ j.policy_detail.approvers.join(', ') }}</template>
            <template v-if="j.policy_detail?.delay_minutes"> · {{ j.policy_detail.delay_minutes }} min delay</template>
            <template v-if="j.policy_detail?.window"> · window {{ j.policy_detail.window.name }} ({{ j.policy_detail.window.days.join(' ') }} {{ j.policy_detail.window.start }}–{{ j.policy_detail.window.end }} {{ j.policy_detail.window.timezone }})</template>
          </span>
        </div>
        <ul v-if="j.approvals?.length" class="alist">
          <li v-for="(a, n) in j.approvals" :key="n"><b>{{ a.by }}</b> <span class="faint small">{{ stamp(a.at) }}</span><span v-if="a.comment" class="small"> · {{ a.comment }}</span></li>
        </ul>
        <div v-if="j.replaces && j.approvals?.length" class="small faint">Carried over from job <RouterLink :to="`/jobs/${j.replaces}`">#{{ j.replaces }}</RouterLink>: the same changes.</div>
        <div v-if="j.status === 'approved'" class="okbox small" style="margin-top: 8px">Approved. Applies {{ stamp(j.apply_after) }}<template v-if="j.policy_detail?.window"> (change window)</template>.</div>
        <div v-if="j.status === 'planned' && j.can_apply" class="row" style="margin-top: 10px">
          <input v-if="j.can_approve" v-model="comment" type="text" placeholder="Comment (optional)" aria-label="Comment" style="max-width: 420px">
          <span v-else class="small muted">You can't approve this: {{ j.why_not }}.</span>
        </div>
        <div v-if="j.result?.discarded_by" class="small muted" style="margin-top: 8px">Discarded by {{ j.result.discarded_by }}<template v-if="j.result.comment">: {{ j.result.comment }}</template></div>
      </div>

      <div v-if="j.mode === 'plan' && j.plan.length" class="plan" style="margin-bottom: 16px">
        <div class="sect-head">
          <h2>Plan</h2>
          <span v-for="(n, op) in counts" :key="op" class="op" :class="`op-${op}`">{{ n }} {{ op }}</span>
          <span class="grow"></span>
          <span v-if="canPick" class="small muted">Untick what shouldn’t be applied{{ skip.size ? ` · ${skip.size} left out` : '' }}</span>
          <button v-if="j.plan.some((c) => c.before != null || c.after != null)" class="btn small ghost"
                  @click="open = open.size ? new Set() : new Set(j.plan.map((c, n) => (c.before != null || c.after != null ? n : -1)).filter((n) => n >= 0))">{{ open.size ? 'Collapse all' : 'Expand all' }}</button>
        </div>
        <section v-for="g in grouped" :key="g.kind" class="pg">
          <div class="pg-head">
            <input v-if="canPick" type="checkbox" :checked="g.items.some((c) => !skip.has(c.n))" :indeterminate="g.items.some((c) => skip.has(c.n)) && g.items.some((c) => !skip.has(c.n))"
                   :aria-label="`Apply every ${g.kind} change`" @change="flipGroup(g)">
            <b>{{ g.kind }}</b><span class="faint small">{{ g.items.length }}</span>
          </div>
          <div class="list">
            <div v-for="c in g.items" :key="c.n" class="li pli" :class="{ off: skip.has(c.n) }">
              <input v-if="canPick" type="checkbox" :checked="!skip.has(c.n)" :aria-label="`Apply ${c.op} ${c.name}`" @change="flip(c.n)">
              <span v-else-if="c.skipped" class="tag" title="Left out at approval">left out</span>
              <div class="li-main">
                <div class="li-title"><span class="op" :class="`op-${c.op}`">{{ c.op }}</span><span class="mono">{{ c.name }}</span>
                  <span class="small muted">{{ c.summary }}</span></div>
                <template v-if="open.has(c.n) && (c.before != null || c.after != null)">
                  <Diff v-if="c.op === 'update' && structured(c)" :rows="fields(c)" style="margin-top: 8px" />
                  <div v-else class="ba">
                    <div v-if="c.before != null"><h3>Before</h3><pre class="code del">{{ fmt(c.before) }}</pre></div>
                    <div v-if="c.after != null"><h3>After</h3><pre class="code add">{{ fmt(c.after) }}</pre></div>
                  </div>
                </template>
              </div>
              <button v-if="c.before != null || c.after != null" class="btn small ghost" :aria-expanded="open.has(c.n)" @click="toggle(c.n)">
                {{ open.has(c.n) ? 'Hide' : c.op === 'update' && structured(c) ? `${fields(c).length} field${fields(c).length === 1 ? '' : 's'}` : 'Details' }}</button>
            </div>
          </div>
        </section>
      </div>
      <div v-else-if="j.mode === 'plan' && !RUNNING.includes(j.status) && j.status !== 'failed'" class="card" style="margin-bottom: 16px">
        Nothing to change: the target already matches the inventory.
      </div>

      <div class="card">
        <div class="row" style="margin-bottom: 8px"><h3 style="margin: 0">Log</h3><span class="grow"></span>
          <span v-if="RUNNING.includes(j.status)" class="small muted"><span class="live"></span> live</span>
          <label class="check small"><input v-model="follow" type="checkbox"> follow</label></div>
        <pre ref="logEl" class="code">{{ j.log || (RUNNING.includes(j.status) ? 'Waiting for output…' : '(empty)') }}</pre>
      </div>
    </template>
  </div>
</template>

<style scoped>
.pg { margin-top: 12px; }
.pg-head { display: flex; align-items: center; gap: 8px; margin: 0 0 6px 2px; }
.pli { align-items: flex-start; }
.pli input[type=checkbox] { margin-top: 4px; }
.pli.off .li-main { opacity: .45; text-decoration: line-through; text-decoration-color: var(--faint); }
.ba { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin-top: 8px; }
.ba h3 { margin: 0 0 4px; font-size: calc(12px * var(--text)); color: var(--muted); }
.approvals .alist { margin: 10px 0 4px; padding-left: 18px; }
.okbox { padding: 8px 12px; border: 1px solid var(--ok); border-radius: 8px; color: var(--ok); }
.ba pre { max-height: 300px; white-space: pre-wrap; word-break: break-all; }
pre.del { border-color: rgba(248, 113, 113, .35); }
pre.add { border-color: rgba(74, 222, 128, .35); }
.live { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--ok); animation: pulse 1.2s infinite; }
@keyframes pulse { 50% { opacity: .3; } }
</style>
