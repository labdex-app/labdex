<script setup>
// Swatches plus a free colour picker. Empty = no colour of its own (noneColour shows what that falls back to).
defineProps({ modelValue: { type: String, default: '' }, noneColour: { type: String, default: '' }, noneTitle: { type: String, default: 'no colour' } })
const emit = defineEmits(['update:modelValue'])
const SWATCHES = ['#e879f9', '#a78bfa', '#60a5fa', '#22d3ee', '#4ade80', '#a3e635', '#fbbf24', '#fb923c', '#f87171', '#a3a3ab']
</script>

<template>
  <div class="cp">
    <button type="button" class="sw none" :class="{ on: !modelValue }" :title="noneTitle" :aria-label="noneTitle"
            :style="noneColour ? { background: noneColour } : {}" @click="emit('update:modelValue', '')"></button>
    <button v-for="c in SWATCHES" :key="c" type="button" class="sw" :class="{ on: modelValue === c }" :style="{ background: c }"
            :aria-label="c" @click="emit('update:modelValue', c)"></button>
    <span v-if="modelValue && !SWATCHES.includes(modelValue)" class="sw on" :style="{ background: modelValue }" :title="modelValue"></span>
    <input :value="modelValue || '#888888'" type="color" aria-label="Pick a colour" class="picker" @input="emit('update:modelValue', $event.target.value)">
  </div>
</template>

<style scoped>
.cp { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.sw { width: 22px; height: 22px; border-radius: 6px; border: 2px solid transparent; cursor: pointer; padding: 0; box-shadow: inset 0 0 0 1px rgba(0,0,0,.25); }
.sw.none { background: repeating-linear-gradient(135deg, var(--raise) 0 4px, var(--line) 4px 8px); }
.sw.on { border-color: var(--ink); }
.picker { width: 22px; height: 22px; padding: 0; border: 0; border-radius: 6px; cursor: pointer; appearance: none; -webkit-appearance: none; overflow: hidden;
  background: conic-gradient(#f87171, #fbbf24, #4ade80, #22d3ee, #60a5fa, #a78bfa, #e879f9, #f87171); box-shadow: inset 0 0 0 1px rgba(0,0,0,.25); }
.picker::-webkit-color-swatch-wrapper { padding: 0; }
.picker::-webkit-color-swatch { border: 0; opacity: 0; } /* the rainbow says "any colour"; the swatches show the picked one */
.picker::-moz-color-swatch { border: 0; opacity: 0; }
</style>
