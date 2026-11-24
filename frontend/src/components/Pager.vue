<script setup>
// The one pager for every long list: "1–50 of 315 hosts  ‹ 1 2 … 7 ›". Inside a .card or .list it
// becomes the card's footer. Lists paged by the server without a total (History) pass `more`
// instead of `total` and get ‹ page n ›.
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'
import { computed } from 'vue'
const props = defineProps({ total: { type: Number, default: null }, size: { type: Number, default: 50 }, modelValue: { type: Number, default: 0 },
  more: { type: Boolean, default: false }, // server paging: is there a page after this one
  unit: { type: String, default: '' } }) // what's counted ("releases"), after the total
const emit = defineEmits(['update:modelValue'])
const known = computed(() => props.total !== null)
const pages = computed(() => (known.value ? Math.max(1, Math.ceil(props.total / props.size)) : props.modelValue + (props.more ? 2 : 1)))
const shown = computed(() => {
  const n = pages.value, p = props.modelValue
  const out = new Set([0, n - 1, p - 1, p, p + 1])
  return [...out].filter((x) => x >= 0 && x < n).sort((a, b) => a - b)
})
const go = (p) => emit('update:modelValue', p)
</script>

<template>
  <nav v-if="pages > 1" class="pager" aria-label="Pages">
    <span v-if="known" class="pg-count">{{ modelValue * size + 1 }}–{{ Math.min(total, (modelValue + 1) * size) }} of {{ total }}{{ unit ? ` ${unit}` : '' }}</span>
    <span v-else class="pg-count">page {{ modelValue + 1 }}</span>
    <span class="grow"></span>
    <button class="btn small ghost" :disabled="modelValue === 0" aria-label="Previous page" @click="go(modelValue - 1)"><ChevronLeft :size="14" /></button>
    <template v-if="known">
      <template v-for="(p, i) in shown" :key="p">
        <span v-if="i && p - shown[i - 1] > 1" class="faint">…</span>
        <button class="btn small" :class="p === modelValue ? 'on' : 'ghost'" :aria-current="p === modelValue ? 'page' : undefined" @click="go(p)">{{ p + 1 }}</button>
      </template>
    </template>
    <button class="btn small ghost" :disabled="modelValue >= pages - 1" aria-label="Next page" @click="go(modelValue + 1)"><ChevronRight :size="14" /></button>
  </nav>
</template>
