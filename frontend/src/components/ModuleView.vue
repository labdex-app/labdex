<script setup>
// Renders what a plugin's host_view() returns: summary chips, tables, lists, a note.
defineProps({ view: Object })
const tone = (t) => ({ ok: 'ok', warn: 'warn', err: 'err' }[t] || '')
</script>

<template>
  <div v-if="view?.error" class="errbox small">{{ view.error }}</div>
  <template v-else-if="view">
    <div v-if="view.summary?.length" class="row mb-3">
      <span v-for="s in view.summary" :key="s.label" class="sumchip" :class="tone(s.tone)"><b>{{ s.value }}</b> {{ s.label }}</span>
    </div>
    <div v-for="(sec, n) in view.sections || []" :key="n" class="sec">
      <h3>{{ sec.title }}</h3>
      <template v-if="sec.type === 'table'">
        <div v-if="sec.rows?.length" class="table-wrap">
          <table class="mv">
            <thead><tr><th v-for="c in sec.columns" :key="c.key">{{ c.label }}</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in sec.rows" :key="i" :class="`t-${tone(r._tone)}`">
                <td v-for="c in sec.columns" :key="c.key" :class="{ mono: ['name', 'value', 'source', 'destination', 'port', 'host'].includes(c.key) }">
                  <RouterLink v-if="c.link === 'host' && r[c.key]" :to="`/hosts/${r[c.key]}`">{{ r[c.key] }}</RouterLink>
                  <template v-else>{{ r[c.key] }}</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="faint small m-0">{{ sec.empty || 'Nothing.' }}</p>
      </template>
      <ul v-else-if="sec.type === 'list'" class="plain">
        <li v-for="x in sec.items" :key="x" class="mono small">{{ x }}</li>
        <li v-if="!sec.items?.length" class="faint small">—</li>
      </ul>
      <dl v-else-if="sec.type === 'kv'" class="kv small">
        <template v-for="(v, k) in sec.items" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
      </dl>
      <pre v-else-if="sec.type === 'text'" class="code">{{ sec.text }}</pre>
    </div>
    <p v-if="view.note" class="hint">{{ view.note }}</p>
  </template>
</template>

<style scoped>
.sumchip { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--raise); color: var(--muted); }
.sumchip b { color: var(--ink); }
.sumchip.ok b { color: var(--ok); } .sumchip.warn b { color: var(--warn); } .sumchip.err b { color: var(--err); }
.sec + .sec { margin-top: 16px; }
table.mv td { font-size: 12.5px; padding: 6px 10px; }
tr.t-ok td:last-child, tr.t-ok td:nth-last-child(2) { color: var(--ok); }
tr.t-warn td { background: rgba(251, 191, 36, .05); }
tr.t-err td { background: rgba(248, 113, 113, .07); }
tr.t-err td:nth-last-child(-n+2) { color: var(--err); }
</style>
