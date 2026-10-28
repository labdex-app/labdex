// The keyboard and ARIA side of a text field with a list of suggestions under it (HostPicker,
// ChipInput): the arrows move the highlight, the field names the highlighted option for screen
// readers, and leaving the field closes the list only once a click on it has landed.
import { ref } from 'vue'

let seq = 0

export function useCombo(count) {
  const id = `combo-${++seq}`
  const active = ref(0)
  const optionId = (i) => `${id}-${i}`
  // ArrowDown / ArrowUp within the list; true when the key was one of them
  function move(e) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return false
    e.preventDefault()
    active.value = Math.max(0, Math.min(active.value + (e.key === 'ArrowDown' ? 1 : -1), count() - 1))
    return true
  }
  // a click on an option fires after the field's blur: wait for it before closing
  const afterBlur = (fn) => window.setTimeout(fn, 150)
  const fieldAttrs = (open) => ({ role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': open, 'aria-controls': id,
    'aria-activedescendant': open ? optionId(active.value) : undefined })
  return { id, active, optionId, move, afterBlur, fieldAttrs }
}
