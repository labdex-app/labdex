<script setup>
// The last 48 hours of a host's combined state, one cell per hour, oldest on the left.
// A cell shows the worst state seen in that hour; grey where nothing was recorded yet.
defineProps({ hours: { type: Array, default: () => [] } })
const TEXT = { up: 'up', down: 'down', unknown: 'no data', none: 'not recorded' }
const at = (n, len) => (len - n === 1 ? 'this hour' : `${len - n - 1} h ago`)
</script>

<template>
  <div class="strip" role="img" :aria-label="`Health over the last ${hours.length} hours: ${hours.filter((h) => h === 'down').length} hours with an outage`">
    <span v-for="(h, n) in hours" :key="n" class="cell" :class="h" :title="`${at(n, hours.length)}: ${TEXT[h] || h}`"></span>
  </div>
</template>

<style scoped>
.strip { display: flex; gap: 2px; height: 22px; }
.cell { flex: 1; min-width: 2px; border-radius: 2px; background: var(--raise); }
.cell.up { background: var(--ok); }
.cell.down { background: var(--err); }
.cell.unknown { background: var(--line-strong); }
</style>
