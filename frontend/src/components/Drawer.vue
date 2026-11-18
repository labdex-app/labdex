<script setup>
// A panel on the right for the selected item of a list (list-detail): the list stays in view
// and usable. Esc or the cross closes it; focus moves in when it opens and back when it closes.
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { X } from 'lucide-vue-next'

defineProps({ label: { type: String, default: '' }, title: { type: String, default: '' } })
const emit = defineEmits(['close'])
const el = ref(null)
let back = null

function key(e) { if (e.key === 'Escape' && !document.querySelector('.dlg-back')) emit('close') }
onMounted(() => {
  back = document.activeElement
  nextTick(() => el.value?.focus())
  window.addEventListener('keydown', key)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', key)
  if (back && document.contains(back)) back.focus?.()
})
</script>

<template>
  <aside ref="el" class="drawer" tabindex="-1" :aria-label="title || label">
    <header class="dw-head">
      <div class="dw-t">
        <div v-if="label" class="label">{{ label }}</div>
        <h2 class="mono">{{ title }}</h2>
      </div>
      <slot name="head" />
      <button class="btn icon" aria-label="Close" title="Close (Esc)" @click="emit('close')"><X :size="17" /></button>
    </header>
    <div class="dw-body"><slot /></div>
    <footer v-if="$slots.foot" class="dw-foot"><slot name="foot" /></footer>
  </aside>
</template>

<style scoped>
.drawer { position: fixed; z-index: 14; top: 50px; bottom: 28px; right: 0; width: min(500px, 100vw); display: flex; flex-direction: column;
  background: var(--card); border-left: 1px solid var(--line); box-shadow: -12px 0 32px var(--shadow); }
.drawer:focus { outline: none; }
.dw-head { display: flex; align-items: flex-start; gap: 10px; padding: 18px 18px 12px 22px; border-bottom: 1px solid var(--line); }
.dw-t { flex: 1; min-width: 0; }
.dw-t h2 { margin: 2px 0 0; font-size: calc(17px * var(--text)); overflow-wrap: anywhere; }
.dw-body { flex: 1; overflow-y: auto; padding: 16px 22px 22px; }
.dw-foot { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 12px 22px; border-top: 1px solid var(--line); background: var(--panel); }
@media (max-width: 860px) { .drawer { top: 0; bottom: 0; z-index: 40; } }
</style>
