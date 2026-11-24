<script setup>
// A small line of the last 48 hours: CPU, memory, disk or network from a host's agent.
// Points are {at, v}; a gap (v null, or more than 15 minutes without a sample) breaks the line.
import { computed } from 'vue'

const props = defineProps({
  points: { type: Array, default: () => [] },
  max: { type: Number, default: null }, // 100 for percentages; null: the highest value
  label: { type: String, default: '' },
  unit: { type: String, default: '%' },
})
const W = 120
const H = 28
const GAP = 15 * 60e3
const path = computed(() => {
  const pts = props.points.filter((p) => p.v != null)
  if (pts.length < 2) return ''
  const t0 = new Date(props.points[0].at).getTime()
  const t1 = new Date(props.points[props.points.length - 1].at).getTime()
  const top = props.max ?? Math.max(...pts.map((p) => p.v), 1)
  let d = ''
  let prev = null
  for (const p of props.points) {
    const t = new Date(p.at).getTime()
    if (p.v == null) { prev = null; continue }
    const x = ((t - t0) / Math.max(t1 - t0, 1)) * W
    const y = H - 1 - (Math.min(p.v, top) / top) * (H - 2)
    d += `${prev === null || t - prev > GAP ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    prev = t
  }
  return d
})
const peak = computed(() => {
  const v = props.points.map((p) => p.v).filter((x) => x != null)
  return v.length ? Math.max(...v) : null
})
</script>

<template>
  <svg v-if="path" class="spark" :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" role="img"
       :aria-label="`${label} over the last 48 hours, peak ${Math.round(peak)}${unit}`">
    <title>{{ label }}: peak {{ Math.round(peak) }}{{ unit }} in 48 h</title>
    <path :d="path" fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round" />
  </svg>
  <span v-else class="spark none small faint">no history yet</span>
</template>

<style scoped>
.spark { display: block; width: 100%; height: 28px; color: var(--info); }
.spark.none { display: block; height: 28px; line-height: 28px; }
</style>
