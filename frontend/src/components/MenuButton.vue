<script setup>
// A button that opens a short list of actions: items [{label, icon?, danger?, disabled?, run}].
// Closes on a pick, a click outside, or Escape; arrow keys move between items.
import { ChevronDown } from 'lucide-vue-next'
import { nextTick, onBeforeUnmount, ref } from 'vue'

defineProps({ items: { type: Array, required: true }, label: { type: String, default: '' }, icon: { type: [Object, Function], default: null },
  title: { type: String, default: '' }, align: { type: String, default: 'right' }, small: { type: Boolean, default: true } })
const open = ref(false)
const root = ref(null)
const list = ref(null)

function outside(e) { if (root.value && !root.value.contains(e.target)) close() }
async function toggle() {
  open.value = !open.value
  if (open.value) {
    document.addEventListener('pointerdown', outside)
    await nextTick()
    list.value?.querySelector('button:not(:disabled)')?.focus()
  } else document.removeEventListener('pointerdown', outside)
}
function close() { open.value = false; document.removeEventListener('pointerdown', outside) }
function pick(it) { close(); it.run() }
function keys(e) {
  if (e.key === 'Escape') { close(); root.value?.querySelector('.mb-trigger')?.focus(); return }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  e.preventDefault()
  const btns = [...list.value.querySelectorAll('button:not(:disabled)')]
  const i = btns.indexOf(document.activeElement)
  btns[(i + (e.key === 'ArrowDown' ? 1 : -1) + btns.length) % btns.length]?.focus()
}
onBeforeUnmount(close)
</script>

<template>
  <div ref="root" class="mb" @keydown="keys">
    <button type="button" class="btn mb-trigger" :class="{ small, icon: !label }" :title="title || label" :aria-label="title || label"
            :aria-expanded="open" aria-haspopup="menu" @click="toggle">
      <component :is="icon" v-if="icon" :size="14" /><template v-if="label">{{ label }} <ChevronDown :size="13" class="faint" /></template>
    </button>
    <div v-if="open" ref="list" class="mb-list" :class="align" role="menu">
      <button v-for="it in items" :key="it.label" type="button" role="menuitem" :class="{ danger: it.danger }" :disabled="it.disabled" @click="pick(it)">
        <component :is="it.icon" v-if="it.icon" :size="14" />{{ it.label }}</button>
    </div>
  </div>
</template>

<style scoped>
.mb { position: relative; display: inline-flex; }
.mb-list { position: absolute; top: calc(100% + 4px); z-index: 20; min-width: 190px; background: var(--card); border: 1px solid var(--line-strong);
  border-radius: var(--r-box); padding: 4px; box-shadow: 0 12px 30px var(--shadow); display: flex; flex-direction: column; }
.mb-list.right { right: 0; }
.mb-list.left { left: 0; }
.mb-list button { display: flex; align-items: center; gap: 8px; text-align: left; font: inherit; font-size: 13px; color: var(--ink); background: none;
  border: 0; border-radius: var(--r-ctl); padding: 7px 10px; cursor: pointer; white-space: nowrap; }
.mb-list button:hover, .mb-list button:focus-visible { background: var(--raise); outline: none; }
.mb-list button.danger { color: var(--err); }
.mb-list button:disabled { opacity: .45; cursor: not-allowed; }
</style>
