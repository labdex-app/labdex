<script setup>
// A sheet of host labels, each with a QR code that opens the host: tick hosts on the list, open
// this page, print. The labels are SVG in millimetres, so they print true on a label printer.
import { Printer } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import FilterSelect from '../components/FilterSelect.vue'

const route = useRoute()
const names = computed(() => String(route.query.hosts || '').split(',').filter(Boolean))
const SIZES = [{ value: '62x29', label: '62 × 29 mm' }, { value: '50x25', label: '50 × 25 mm' }, { value: '89x36', label: '89 × 36 mm' }]
const size = ref('62x29')
const print = () => window.print()
const src = (n) => `/api/hosts/${encodeURIComponent(n)}/label.svg?size=${size.value}`
</script>

<template>
  <div class="page">
    <div class="page-head screen">
      <h1>Labels</h1>
      <span class="sub">{{ names.length }} host{{ names.length === 1 ? '' : 's' }}; the QR code opens the host in labdex.</span>
      <span class="grow"></span>
      <FilterSelect v-model="size" label="Size" :options="SIZES" any="" />
      <button class="btn primary" :disabled="!names.length" @click="print"><Printer :size="15" /> Print</button>
    </div>
    <p v-if="!names.length" class="empty">Tick some hosts on the <RouterLink to="/hosts">Hosts</RouterLink> page and choose Labels.</p>
    <div class="sheet">
      <img v-for="n in names" :key="n" :src="src(n)" :alt="`Label for ${n}`" :class="`s${size}`">
    </div>
    <p class="hint screen">Set the paper size of your label printer to the label size in the print dialog and turn headers and margins off. Set the address of this labdex under Settings › General so the codes work from outside.</p>
  </div>
</template>

<style scoped>
.sheet { display: flex; flex-wrap: wrap; gap: 3mm; }
.sheet img { border: 0.3mm dashed var(--line-strong); background: #fff; }
.s62x29 { width: 62mm; height: 29mm; }
.s50x25 { width: 50mm; height: 25mm; }
.s89x36 { width: 89mm; height: 36mm; }
@media print {
  .screen { display: none !important; }
  .sheet img { border: 0; break-inside: avoid; }
}
</style>
