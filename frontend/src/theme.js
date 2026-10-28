// Light, dark, or whatever the system says, and the text size. Kept in this browser; applied
// before the app mounts, so the page never flashes the other theme or size.
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

// text size: every font size in the UI is a 14px-base size times --text
const SIZES = { compact: 1, comfortable: 15 / 14, large: 16 / 14 }
const TKEY = 'labdex-text'
const readSize = () => { try { const v = localStorage.getItem(TKEY); return v in SIZES ? v : 'comfortable' } catch { return 'comfortable' } }
export const textSize = ref(readSize()) // compact | comfortable | large

export function applyTextSize(s = textSize.value) {
  document.documentElement.style.setProperty('--text', String(SIZES[s] ?? SIZES.comfortable))
}

export function setTextSize(s) {
  textSize.value = s
  try { localStorage.setItem(TKEY, s) } catch { /* */ }
  applyTextSize(s)
}
