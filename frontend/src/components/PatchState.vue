<script setup>
// A host's patch state as a small chip: current, updates waiting, security, reboot, stale, error, unknown.
import { computed } from 'vue'

const props = defineProps({ state: { type: Object, default: null } })
const LABEL = { current: 'up to date', pending: 'updates', security: 'security', reboot: 'reboot', stale: 'stale', error: 'error', unknown: 'no report' }
const text = computed(() => {
  const s = props.state
  if (!s) return LABEL.unknown
  if (s.state === 'pending') return `${s.pending} update${s.pending === 1 ? '' : 's'}`
  if (s.state === 'security') return `${s.security} security`
  return LABEL[s.state] || s.state
})
</script>

<template>
  <span class="pstate" :class="state?.state || 'unknown'">{{ text }}<template v-if="state?.reboot && state.state !== 'reboot'"> · reboot</template></span>
</template>

<style scoped>
.pstate { font-size: calc(11.5px * var(--text)); font-weight: 600; padding: 1px 8px; border-radius: var(--r-ctl); white-space: nowrap;
  background: color-mix(in srgb, var(--faint) 12%, transparent); color: var(--faint); }
.pstate.current { color: var(--ok); background: color-mix(in srgb, var(--ok) 12%, transparent); }
.pstate.pending { color: var(--ink); background: color-mix(in srgb, var(--ink) 8%, transparent); }
.pstate.security, .pstate.error { color: var(--err); background: color-mix(in srgb, var(--err) 12%, transparent); }
.pstate.reboot { color: var(--warn); background: color-mix(in srgb, var(--warn) 14%, transparent); }
</style>
