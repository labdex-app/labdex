<script setup>
// "Loading…" for a page, card, table or toolbar still waiting for its data. It appears only after
// a moment, so an answer that comes quickly never flickers; `inline` sits in a line of text.
import { LoaderCircle } from 'lucide-vue-next'
import { onBeforeUnmount, ref } from 'vue'

defineProps({ label: { type: String, default: 'Loading…' }, inline: { type: Boolean, default: false } })
const shown = ref(false)
const t = window.setTimeout(() => { shown.value = true }, 250)
onBeforeUnmount(() => window.clearTimeout(t))
</script>

<template>
  <component :is="inline ? 'span' : 'div'" class="loading" :class="{ inline, empty: !inline }" role="status">
    <template v-if="shown"><LoaderCircle :size="inline ? 13 : 18" class="spin" />{{ label }}</template>
  </component>
</template>

<style scoped>
.loading.inline { display: inline-flex; align-items: center; gap: 6px; color: var(--faint); font-size: calc(12.5px * var(--text)); }
.loading:not(.inline) { min-height: 86px; }
</style>
