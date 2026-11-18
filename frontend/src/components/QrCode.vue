<script setup>
// A QR code as SVG squares (no HTML injected), sized by its parent. qrcode-generator only
// computes the matrix.
import qrcode from 'qrcode-generator'
import { computed } from 'vue'

const props = defineProps({ text: { type: String, required: true }, level: { type: String, default: 'M' } })
const matrix = computed(() => {
  const q = qrcode(0, props.level)
  q.addData(props.text)
  q.make()
  const n = q.getModuleCount()
  let d = ''
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) if (q.isDark(r, c)) d += `M${c} ${r}h1v1h-1z`
  }
  return { n, d }
})
</script>

<template>
  <!-- a quiet zone of 2 modules around it: enough on a white label -->
  <svg :viewBox="`-2 -2 ${matrix.n + 4} ${matrix.n + 4}`" shape-rendering="crispEdges" role="img" :aria-label="`QR code: ${text}`">
    <path :d="matrix.d" fill="#000" />
  </svg>
</template>
