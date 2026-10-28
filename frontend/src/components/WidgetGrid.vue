<script setup>
// Plugin widgets, drawn by labdex from declarative data (no plugin code in the browser).
import { computed, ref } from 'vue'
import { useQuery } from '../query'
import Loading from './Loading.vue'

// only: {integration, widget} draws just that one (a dashboard card); bare: no margin around it
const props = defineProps({ url: { type: String, required: true }, title: { type: String, default: '' }, only: { type: Object, default: null }, bare: Boolean, flat: Boolean })
const q = useQuery(() => props.url, { staleMs: 30000 })
const items = computed(() => (q.data.value || []).filter((w) => !props.only || (w.integration === props.only.integration && w.id === props.only.widget)))
const hover = ref({})

const TONE = { ok: 'c-green', warn: 'c-yellow', err: 'c-red', '': '' }
const STATE = { up: ['●', 'c-green', 'up'], down: ['●', 'c-red', 'down'], warn: ['▲', 'c-yellow', 'warning'], unknown: ['○', 'faint', 'unknown'] }
const fmt = (v) => (typeof v === 'number' ? (Math.abs(v) >= 100 || Number.isInteger(v) ? Math.round(v).toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 })) : v)

function path(w) {
  const pts = w.data.points || []
  if (pts.length < 2) return { line: '', area: '', min: 0, max: 0 }
  const min = w.data.min ?? Math.min(...pts)
  const max = w.data.max ?? Math.max(...pts)
  const span = max - min || 1
  const W = 240, H = 44
  const xy = pts.map((p, i) => [(i / (pts.length - 1)) * W, H - 2 - ((p - min) / span) * (H - 4)])
  const line = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('')
  return { line, area: `${line}L${W},${H}L0,${H}Z`, xy, min, max }
}
function onMove(ev, w, key) {
  const pts = w.data.points || []
  const r = ev.currentTarget.getBoundingClientRect()
  const i = Math.max(0, Math.min(pts.length - 1, Math.round(((ev.clientX - r.left) / r.width) * (pts.length - 1))))
  hover.value = { ...hover.value, [key]: i }
}
const key = (w) => `${w.integration}/${w.id}`
</script>

<template>
  <section v-if="items.length" class="wgrid-wrap" :class="{ bare, flat }">
    <h2 v-if="title" class="wtitle">{{ title }}</h2>
    <div class="wgrid">
      <div v-for="w in items" :key="key(w)" class="card widget" :class="{ wide: w.size === 'wide' }">
        <div class="whead">
          <component :is="w.data?.link ? 'RouterLink' : 'span'" :to="w.data?.link" class="wlabel">{{ w.label }}</component>
          <RouterLink :to="`/integrations/${w.integration}`" class="wsrc">{{ w.integration }}</RouterLink>
        </div>
        <Loading v-if="!w.data" inline />
        <div v-else-if="w.data.kind === 'error'" class="small c-red werr" :title="w.data.error">✕ {{ w.data.error }}</div>

        <template v-else-if="w.data.kind === 'stat'">
          <div class="wnum" :class="TONE[w.data.tone]">{{ fmt(w.data.value) }}<span v-if="w.data.unit" class="wunit">{{ w.data.unit }}</span></div>
        </template>

        <ul v-else-if="w.data.kind === 'status'" class="plain wlist">
          <li v-for="(i, n) in w.data.items" :key="n">
            <span :class="STATE[i.state][1]" :aria-label="STATE[i.state][2]">{{ STATE[i.state][0] }}</span>
            <component :is="i.link ? 'RouterLink' : 'span'" :to="i.link" class="grow ell">{{ i.label }}</component>
            <span class="small faint ell">{{ i.detail || STATE[i.state][2] }}</span>
          </li>
          <li v-if="!w.data.items.length" class="faint small">nothing to show</li>
        </ul>

        <ul v-else-if="w.data.kind === 'list'" class="plain wlist">
          <li v-for="(i, n) in w.data.items" :key="n">
            <component :is="i.link ? 'RouterLink' : 'span'" :to="i.link" class="mono small">{{ i.label }}</component>
            <span class="grow"></span>
            <span class="small ell" :class="TONE[i.tone]">{{ i.value }}</span>
          </li>
          <li v-if="!w.data.items.length" class="faint small">nothing to show</li>
        </ul>

        <div v-else-if="w.data.kind === 'table'" class="table-wrap">
          <table class="wtable">
            <thead><tr><th v-for="c in w.data.columns" :key="c">{{ c }}</th></tr></thead>
            <tbody><tr v-for="(r, n) in w.data.rows" :key="n"><td v-for="(c, m) in r" :key="m" class="small">{{ fmt(c) }}</td></tr></tbody>
          </table>
        </div>

        <template v-else-if="w.data.kind === 'sparkline'">
          <div class="wnum">{{ fmt(hover[key(w)] != null ? w.data.points[hover[key(w)]] : (w.data.value ?? w.data.points.at(-1))) }}<span v-if="w.data.unit" class="wunit">{{ w.data.unit }}</span></div>
          <svg class="spark" viewBox="0 0 240 44" preserveAspectRatio="none" role="img" :aria-label="`${w.label}: ${w.data.points.length} values`"
               @mousemove="onMove($event, w, key(w))" @mouseleave="hover = { ...hover, [key(w)]: null }">
            <path :d="path(w).area" class="sarea" />
            <path :d="path(w).line" class="sline" vector-effect="non-scaling-stroke" />
            <circle v-if="hover[key(w)] != null && path(w).xy" :cx="path(w).xy[hover[key(w)]][0]" :cy="path(w).xy[hover[key(w)]][1]" r="3" class="sdot" />
          </svg>
        </template>

        <div v-if="w.data?.sub" class="small faint wsub">{{ w.data.sub }}</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.werr { overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.wgrid-wrap { margin-bottom: 16px; }
.wgrid-wrap.bare { margin: 0; }
.flat .widget { border: 0; padding: 0; background: none; }
.flat .wgrid { grid-template-columns: minmax(0, 1fr); }
.wtitle { font-size: 14px; margin: 0 0 10px; }
.wgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
.widget { margin: 0; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.widget.wide { grid-column: span 2; }
@media (max-width: 640px) { .widget.wide { grid-column: auto; } }
.whead { display: flex; align-items: baseline; gap: 8px; }
.wlabel { font-size: 13px; font-weight: 500; color: var(--muted); flex: 1; min-width: 0; }
.wsrc { font-size: 11px; color: var(--faint); font-family: var(--mono); }
.wnum { font-size: 26px; font-weight: 650; line-height: 1.15; font-variant-numeric: tabular-nums; }
.wunit { font-size: 13px; font-weight: 500; color: var(--muted); margin-left: 4px; }
.wlist { display: flex; flex-direction: column; gap: 3px; max-height: 220px; overflow: auto; }
.wlist li { display: flex; align-items: center; gap: 8px; min-width: 0; }
.wtable th, .wtable td { padding: 4px 8px; }
.spark { width: 100%; height: 44px; display: block; cursor: crosshair; }
.sline { fill: none; stroke: var(--accent); stroke-width: 2; }
.sarea { fill: var(--accent-faint); stroke: none; }
.sdot { fill: var(--accent); stroke: var(--card); stroke-width: 2; }
.wsub { margin-top: auto; }
</style>
