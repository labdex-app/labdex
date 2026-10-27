// A promise-based confirmation dialog instead of window.confirm:
//   if (await confirm({ title: 'Delete DC_TC?', body: '…', action: 'Delete', danger: true })) …
import { reactive } from 'vue'

export const confirmState = reactive({ open: false, title: '', body: '', action: 'OK', danger: false, resolve: null })

export function confirm({ title, body = '', action = 'OK', danger = false }) {
  return new Promise((resolve) => Object.assign(confirmState, { open: true, title, body, action, danger, resolve }))
}

export function answer(ok) {
  const r = confirmState.resolve
  Object.assign(confirmState, { open: false, resolve: null })
  r?.(ok)
}
