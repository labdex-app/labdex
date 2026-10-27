<script setup>
defineProps({ trust: Object })
const LABEL = { builtin: 'built in', official: 'official', community: 'community', unsigned: 'unsigned', invalid: 'invalid signature' }
</script>
<template>
  <span class="tb" :class="`tb-${trust?.status}`" :title="trust?.detail">{{ LABEL[trust?.status] || trust?.status }}<template v-if="trust?.status === 'community'"> · {{ trust.signer }}</template></span>
</template>
<style scoped>
.tb { font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 999px; white-space: nowrap; border: 1px solid currentColor; }
.tb-builtin { color: var(--muted); }
.tb-official { color: var(--accent-soft); }
.tb-community { color: var(--c-blue); }
.tb-unsigned { color: var(--warn); }
.tb-invalid { color: var(--err); }
</style>
