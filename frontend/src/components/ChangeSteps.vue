<script setup>
// Plan, Review, Apply, Verify: where a plan stands, read from the job's own status.
// Nothing here acts; the buttons sit on the job.
import { computed } from 'vue'
import { Check, X } from 'lucide-vue-next'
import { ago, stamp } from '../store'

const props = defineProps({ job: { type: Object, required: true } })

const steps = computed(() => {
  const j = props.job
  const s = j.status
  const approved = (j.approvals || []).length
  const reviewed = approved > 0 || Boolean(j.applied_by) || ['approved', 'applying', 'applied'].includes(s)
  const empty = !(j.plan ? j.plan.length : j.changes)
  const st = { plan: 'todo', review: 'todo', apply: 'todo', verify: 'todo' }
  const sub = { plan: '', review: '', apply: '', verify: 'plan again: no changes' }
  if (['queued', 'running'].includes(s)) { st.plan = 'now'; sub.plan = 'planning…' } else { st.plan = 'done'; sub.plan = ago(j.created_at) }
  if (s === 'failed' && !reviewed) { st.plan = 'bad'; sub.plan = 'planning failed' }
  if (s === 'succeeded' && empty) {
    // nothing to change: the target already matches, which is what Verify checks
    Object.assign(st, { review: 'skip', apply: 'skip', verify: 'done' })
    Object.assign(sub, { review: 'nothing to review', apply: 'nothing to apply', verify: 'in sync' })
  } else if (s === 'planned') {
    st.review = 'now'
    sub.review = j.needed ? `${approved} of ${j.needed} approvals` : 'waiting for approval'
  } else if (['stale', 'discarded'].includes(s)) {
    st.review = 'skip'
    sub.review = s === 'stale' ? 'out of date' : 'discarded'
  } else if (reviewed) {
    st.review = 'done'
    sub.review = approved ? `${approved} approval${approved === 1 ? '' : 's'}` : 'approved'
    if (s === 'approved') { st.apply = 'now'; sub.apply = j.apply_after ? `at ${stamp(j.apply_after)}` : 'waiting to apply' }
    else if (s === 'applying') { st.apply = 'now'; sub.apply = 'applying…' }
    else if (s === 'applied') { st.apply = 'done'; sub.apply = j.finished_at ? ago(j.finished_at) : 'applied'; st.verify = 'now' }
    else if (s === 'failed') { st.apply = 'bad'; sub.apply = 'failed' }
  }
  return [
    { key: 'plan', label: 'Plan', state: st.plan, sub: sub.plan },
    { key: 'review', label: 'Review', state: st.review, sub: sub.review },
    { key: 'apply', label: 'Apply', state: st.apply, sub: sub.apply },
    { key: 'verify', label: 'Verify', state: st.verify, sub: sub.verify },
  ]
})
const WORD = { done: 'done', now: 'current step', bad: 'failed', skip: 'skipped', todo: 'not yet' }
</script>

<template>
  <ol class="steps" aria-label="Where this change stands">
    <li v-for="(s, n) in steps" :key="s.key" :class="s.state" :aria-current="s.state === 'now' ? 'step' : undefined">
      <span class="mark" :title="WORD[s.state]">
        <Check v-if="s.state === 'done'" :size="13" :stroke-width="3" /><X v-else-if="s.state === 'bad'" :size="13" :stroke-width="3" /><template v-else>{{ n + 1 }}</template>
      </span>
      <span class="txt"><b>{{ s.label }}</b><span class="small muted">{{ s.sub }}</span><span class="sr">({{ WORD[s.state] }})</span></span>
    </li>
  </ol>
</template>

<style scoped>
.steps { display: flex; list-style: none; margin: 0; padding: 0; gap: 0; }
.steps li { flex: 1; display: flex; align-items: flex-start; gap: 10px; position: relative; min-width: 0; }
/* the line to the next step, green once this one is done */
.steps li:not(:last-child)::after { content: ''; flex: 1; height: 2px; background: var(--line); margin: 11px 12px 0 4px; min-width: 16px; }
.steps li.done:not(:last-child)::after { background: var(--ok); }
.mark { flex: none; width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font: 500 calc(11.5px * var(--text)) var(--mono);
  border: 1.5px solid var(--line-strong); color: var(--faint); background: var(--card); }
.done .mark { background: var(--ok); border-color: var(--ok); color: var(--card); }
.now .mark { border-color: var(--accent); color: var(--accent-soft); background: var(--accent-faint); }
.bad .mark { background: var(--err); border-color: var(--err); color: var(--card); }
.skip .mark { border-style: dashed; }
.txt { display: flex; flex-direction: column; line-height: 1.3; white-space: nowrap; }
.txt b { font-weight: 500; }
.skip .txt b, .todo .txt b { color: var(--muted); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media (max-width: 700px) {
  .steps { flex-direction: column; gap: 10px; }
  .steps li:not(:last-child)::after { display: none; }
}
</style>
