// Light, dark, or whatever the system says. Kept in this browser; applied before the app
// mounts, so the page never flashes the other theme.
import { ref } from 'vue'

const KEY = 'labdex-theme'
const read = () => { try { return localStorage.getItem(KEY) || 'system' } catch { return 'system' } }
export const theme = ref(read()) // system | light | dark
const media = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null
// what's showing now, for code that draws with colours (the topology)
export const shown = ref('dark')

export function applyTheme(t = theme.value) {
  const el = document.documentElement
  if (t === 'light' || t === 'dark') el.dataset.theme = t
  else delete el.dataset.theme
  shown.value = t === 'system' ? (media?.matches ? 'light' : 'dark') : t
  const meta = document.querySelector('meta[name="color-scheme"]')
  if (meta) meta.content = t === 'system' ? 'dark light' : t
}

export function setTheme(t) {
  theme.value = t
  try { localStorage.setItem(KEY, t) } catch { /* */ }
  applyTheme(t)
}

media?.addEventListener?.('change', () => { if (theme.value === 'system') applyTheme('system') })
