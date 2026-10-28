<script setup>
// A tag in its colour (from the tag list), everywhere tags show.
import { computed } from 'vue'
import { tagColour } from '../store'
const props = defineProps({ name: { type: String, required: true }, colour: { type: String, default: null } })
const c = computed(() => (props.colour !== null ? props.colour : tagColour(props.name)))
</script>

<template>
  <span class="tagc" :style="c ? { '--tc': c } : {}" :class="{ col: c }">{{ name }}</span>
</template>

<style scoped>
.tagc { display: inline-block; font-size: calc(12px * var(--text)); line-height: 18px; padding: 0 8px; border-radius: 999px; border: 1px solid var(--line-strong);
  color: var(--muted); white-space: nowrap; }
.tagc.col { color: color-mix(in srgb, var(--tc) var(--zone-mix), var(--ink)); border-color: color-mix(in srgb, var(--tc) 50%, transparent); background: color-mix(in srgb, var(--tc) 10%, transparent); }
</style>
