<script setup>
// Page through a long list: « 1 2 … n », with how many there are.
import { computed } from 'vue'
const props = defineProps({ total: { type: Number, required: true }, size: { type: Number, default: 20 }, modelValue: { type: Number, default: 0 } })
const emit = defineEmits(['update:modelValue'])
const pages = computed(() => Math.max(1, Math.ceil(props.total / props.size)))
const shown = computed(() => {
  const n = pages.value, p = props.modelValue
  const out = new Set([0, n - 1, p - 1, p, p + 1])
  return [...out].filter((x) => x >= 0 && x < n).sort((a, b) => a - b)
})
</script>

<template>
  <div v-if="pages > 1" class="pager">
    <span class="small faint">{{ modelValue * size + 1 }}–{{ Math.min(total, (modelValue + 1) * size) }} of {{ total }}</span>
    <span class="grow"></span>
    <button class="btn small ghost" :disabled="modelValue === 0" aria-label="Previous page" @click="emit('update:modelValue', modelValue - 1)">‹</button>
    <template v-for="(p, i) in shown" :key="p">
      <span v-if="i && p - shown[i - 1] > 1" class="faint">…</span>
      <button class="btn small" :class="p === modelValue ? 'primary' : 'ghost'" @click="emit('update:modelValue', p)">{{ p + 1 }}</button>
    </template>
    <button class="btn small ghost" :disabled="modelValue >= pages - 1" aria-label="Next page" @click="emit('update:modelValue', modelValue + 1)">›</button>
  </div>
</template>

<style scoped>
.pager { display: flex; align-items: center; gap: 4px; margin-top: 10px; }
</style>
