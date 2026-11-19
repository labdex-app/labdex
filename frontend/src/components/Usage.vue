<script setup>
// A percentage as a short bar and its number: CPU, memory or disk use from a host's agent.
// Amber from 75 %, red from 90 %, so a full disk stands out in a column of them.
import { computed } from 'vue'

const props = defineProps({ value: { type: Number, default: null }, label: { type: String, default: '' }, bar: { type: Boolean, default: true } })
const tone = computed(() => (props.value >= 90 ? 'err' : props.value >= 75 ? 'warn' : 'ok'))
</script>

<template>
  <span v-if="value === null || value === undefined" class="faint" :title="label ? `No ${label} reported` : undefined">—</span>
  <span v-else class="use" :class="tone" :aria-label="label ? `${label} ${Math.round(value)} percent` : undefined">
    <span v-if="bar" class="u-bar" aria-hidden="true"><span :style="{ width: `${Math.min(100, Math.max(0, value))}%` }"></span></span>
    <span class="mono u-n">{{ Math.round(value) }}%</span>
  </span>
</template>

<style scoped>
.use { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
.u-bar { flex: 1; min-width: 40px; max-width: 90px; height: 4px; background: var(--raise); border-radius: 2px; overflow: hidden; }
.u-bar > span { display: block; height: 100%; background: var(--info); }
.warn .u-bar > span { background: var(--warn); }
.err .u-bar > span { background: var(--err); }
.u-n { font-size: calc(12px * var(--text)); min-width: 3ch; text-align: right; }
.err .u-n { color: var(--err); }
</style>
