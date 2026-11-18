<script setup>
// YAML with line numbers, coloured by token; with `before` it's a line diff (+ added, − removed)
// and the counts sit in the header beside the file name.
import { computed } from 'vue'
import { diffLines, yamlTokens } from '../linediff'

const props = defineProps({
  after: { type: String, default: '' },
  before: { type: String, default: null }, // null: no diff, just the text
  file: { type: String, default: '' },
})
const rows = computed(() => {
  const lines = props.before === null
    ? props.after.replace(/\n$/, '').split('\n').map((text) => ({ op: ' ', text }))
    : diffLines(props.before, props.after)
  let n = 0
  return lines.map((l) => ({ ...l, n: l.op === '-' ? '' : ++n, tokens: yamlTokens(l.text) }))
})
const added = computed(() => rows.value.filter((r) => r.op === '+').length)
const removed = computed(() => rows.value.filter((r) => r.op === '-').length)
</script>

<template>
  <div class="codebox">
    <div v-if="file || before !== null" class="cb-head">
      <span class="mono cb-file">{{ file }}</span>
      <span v-if="before !== null" class="mono cb-count"><span class="add">+{{ added }}</span> <span class="del">−{{ removed }}</span></span>
    </div>
    <div class="cb-body" role="table" :aria-label="file || 'YAML'">
      <div v-for="(r, i) in rows" :key="i" class="cb-line" :class="{ add: r.op === '+', del: r.op === '-' }" role="row">
        <span class="cb-n" aria-hidden="true">{{ r.n }}</span>
        <span class="cb-op" :aria-label="r.op === '+' ? 'added' : r.op === '-' ? 'removed' : undefined">{{ r.op === '-' ? '−' : r.op.trim() }}</span>
        <span class="cb-t"><span v-for="(t, k) in r.tokens" :key="k" :class="t.t && `tk-${t.t}`">{{ t.s }}</span></span>
      </div>
      <div v-if="before !== null && !added && !removed" class="cb-same">No change.</div>
    </div>
  </div>
</template>

<style scoped>
.codebox { border: 1px solid var(--line); border-radius: var(--r-box); overflow: hidden; background: var(--card); }
.cb-head { display: flex; align-items: center; gap: 10px; padding: 9px 14px; border-bottom: 1px solid var(--line); background: var(--panel); }
.cb-file { flex: 1; min-width: 0; font-weight: 600; font-size: calc(12px * var(--text)); overflow-wrap: anywhere; }
.cb-count { font-size: calc(12px * var(--text)); }
.cb-count .add { color: var(--ok); } .cb-count .del { color: var(--err); }
.cb-body { font-family: var(--mono); font-size: calc(12.5px * var(--text)); line-height: 1.75; padding: 4px 0; overflow-x: auto; }
.cb-line { display: grid; grid-template-columns: 40px 16px minmax(0, 1fr); padding-right: 14px; min-width: max-content; }
.cb-n { text-align: right; color: var(--faint); padding-right: 10px; user-select: none; }
.cb-op { color: var(--faint); user-select: none; }
.cb-t { white-space: pre; }
.cb-line.add { background: color-mix(in srgb, var(--ok) 10%, transparent); }
.cb-line.add .cb-op { color: var(--ok); }
.cb-line.del { background: color-mix(in srgb, var(--err) 10%, transparent); }
.cb-line.del .cb-op { color: var(--err); }
.cb-same { padding: 6px 14px; color: var(--muted); font-family: var(--sans); }
.tk-key { color: var(--code-key); }
.tk-str { color: var(--code-str); }
.tk-num { color: var(--code-num); }
.tk-bool { color: var(--code-bool); }
.tk-comment { color: var(--faint); font-style: italic; }
</style>
