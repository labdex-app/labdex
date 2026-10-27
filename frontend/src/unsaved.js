// Edit pages: don't lose changes by leaving, and Ctrl/⌘ S saves.
//   const unsaved = useUnsaved(dirty, save)
//   unsaved.release()  // before navigating away after a save or delete
import { onBeforeUnmount, onMounted, ref as _ref, toValue, watch as _watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { confirm } from './confirm'

export function useUnsaved(dirty, save) {
  let free = false
  const isDirty = () => !free && toValue(dirty)

  onBeforeRouteLeave(async () => {
    if (!isDirty()) return true
    return confirm({ title: 'Leave without saving?', body: 'Nothing is applied. Your changes stay as a draft in this browser, and the page offers them back next time.', action: 'Leave' })
  })
  function beforeUnload(e) {
    if (isDirty()) { e.preventDefault(); e.returnValue = '' }
  }
  function key(e) {
    if ((e.key === 's' || e.key === 'S') && (e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey) {
      e.preventDefault() // never the browser's "save page"
      if (toValue(dirty) && !document.querySelector('.dlg-back')) save()
    }
  }
  onMounted(() => { window.addEventListener('beforeunload', beforeUnload); window.addEventListener('keydown', key) })
  onBeforeUnmount(() => { window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('keydown', key) })
  return { release: () => { free = true } }
}

// Drafts: what you changed but didn't save stays in this browser (not on the server: nothing
// is applied, no history, no plans) and is offered back when you open the page again.
//   const draft = useDraft(() => `host:${name}`, () => form, (d) => Object.assign(form, d), dirty, loaded)
//   draft.offer  → {at} when an older draft exists;  draft.restore(), draft.discard(), draft.clear()

const PREFIX = 'labdex-draft:'
const MAX_AGE = 14 * 24 * 3600 * 1000

function readDraft(key) {
  try {
    const d = JSON.parse(localStorage.getItem(PREFIX + key) || 'null')
    if (d && Date.now() - d.at < MAX_AGE) return d
    localStorage.removeItem(PREFIX + key)
  } catch { /* storage may be off: drafts are a convenience */ }
  return null
}

export function useDraft(key, snapshot, apply, dirty, ready) {
  const offer = _ref(null)
  let timer = null
  let paused = false
  const write = () => {
    if (paused || !toValue(ready)) return
    try {
      if (toValue(dirty)) localStorage.setItem(PREFIX + key(), JSON.stringify({ at: Date.now(), data: JSON.parse(JSON.stringify(snapshot())) }))
      else if (!offer.value) localStorage.removeItem(PREFIX + key())
    } catch { /* full or off */ }
  }
  _watch(() => [toValue(dirty), JSON.stringify(snapshot())], () => { clearTimeout(timer); timer = setTimeout(write, 800) })
  _watch(() => toValue(dirty), (d) => { if (!d) paused = false })
  // leaving or closing the tab writes at once, not after the pause
  const now = () => { clearTimeout(timer); write() }
  onBeforeRouteLeave(() => { now(); return true })
  onMounted(() => window.addEventListener('pagehide', now))
  onBeforeUnmount(() => window.removeEventListener('pagehide', now))
  // once the page has what the server says, look for an older draft of it
  _watch(() => toValue(ready), (r) => {
    if (!r) return
    const d = readDraft(key())
    offer.value = d && JSON.stringify(d.data) !== JSON.stringify(JSON.parse(JSON.stringify(snapshot()))) ? { at: d.at, data: d.data } : null
  }, { immediate: true })
  return {
    offer,
    restore() { if (offer.value) { paused = true; apply(offer.value.data); offer.value = null; paused = false; write() } },
    discard() { offer.value = null; try { localStorage.removeItem(PREFIX + key()) } catch { /* */ } },
    // after a save: no draft, and none written until the form is saved-and-clean again (a save
    // that renames navigates away while the old form still looks changed)
    clear() { clearTimeout(timer); paused = true; offer.value = null; try { localStorage.removeItem(PREFIX + key()) } catch { /* */ } },
  }
}
