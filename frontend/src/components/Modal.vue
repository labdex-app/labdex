<script setup>
// A dialog for creating and editing one thing. Esc or the backdrop closes it (unless busy);
// the first field gets focus. Footer actions go in the "foot" slot.
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({ title: { type: String, required: true }, wide: Boolean, busy: Boolean })
const emit = defineEmits(['close'])
const box = ref(null)
let before = null

function close() { if (!props.busy) emit('close') }
function key(e) {
  if (e.key === 'Escape') { e.stopPropagation(); close(); return }
  if (e.key !== 'Tab' || !box.value) return // Tab stays inside the dialog
  const items = [...box.value.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled):not([type=hidden]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
    .filter((el) => el.offsetParent !== null)
  if (!items.length) return
  const first = items[0], last = items[items.length - 1]
  if (e.shiftKey && (document.activeElement === first || !box.value.contains(document.activeElement))) { e.preventDefault(); last.focus() }
  else if (!e.shiftKey && (document.activeElement === last || !box.value.contains(document.activeElement))) { e.preventDefault(); first.focus() }
}
onMounted(async () => {
  before = document.activeElement
  document.addEventListener('keydown', key)
  document.body.style.overflow = 'hidden'
  await nextTick()
  const first = box.value?.querySelector('.dlg-body input:not([type=hidden]):not(:disabled), .dlg-body select, .dlg-body textarea')
  ;(first || box.value)?.focus()
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', key)
  document.body.style.overflow = ''
  before?.focus?.()
})
</script>

<template>
  <Teleport to="body">
    <div class="dlg-back" @mousedown.self="close">
      <div ref="box" class="dlg" :class="{ wide }" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1">
        <div class="dlg-head">
          <h2>{{ title }}</h2>
          <slot name="head" />
          <span class="grow"></span>
          <button type="button" class="btn icon" aria-label="Close" :disabled="busy" @click="close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div class="dlg-body"><slot /></div>
        <div v-if="$slots.foot" class="dlg-foot"><slot name="foot" /></div>
      </div>
    </div>
  </Teleport>
</template>
