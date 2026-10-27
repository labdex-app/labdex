import { reactive } from 'vue'
import { api } from './api'
import { clearQueries, invalidate } from './query'

export const state = reactive({
  auth: null, // /api/auth/status
  meta: null,
  nav: null, // /api/nav: tree, modules, what to show
  lint: null, // { error, warning, info }
  pending: 0, // plans waiting for review
  toast: null, // { text, kind }
})

export async function loadAuth() {
  state.auth = await api.get('/api/auth/status')
  return state.auth
}

export async function loadMeta() {
  state.meta = await api.get('/api/meta')
  return state.meta
}

export async function refreshLint() {
  try {
    state.lint = (await api.get('/api/lint')).summary
  } catch {
    state.lint = null
  }
}

export async function refreshPending() {
  try {
    state.pending = can('integration', 'view') ? (await api.get('/api/jobs?status=planned&limit=200')).length : 0
  } catch {
    state.pending = 0
  }
}

export async function loadNav() {
  try { state.nav = await api.get('/api/nav') } catch { /* keep the old one */ }
}

// One Server-Sent Events stream per tab: plan count always, a job's output when a page watches one.
let es = null
let watched = null // { id, onJob, onLog }
let lastRev = null
function openStream() {
  if (es) es.close()
  if (typeof EventSource === 'undefined') return
  es = new EventSource(watched ? `/api/events?job=${watched.id}` : '/api/events')
  es.addEventListener('pending', (e) => {
    const n = JSON.parse(e.data).count
    if (n !== state.pending) { state.pending = n; loadNav() }
  })
  es.addEventListener('revision', (e) => {
    const rev = JSON.parse(e.data).rev
    if (lastRev !== null && rev !== lastRev) { invalidate(); refreshLint(); loadNav() }
    lastRev = rev
  })
  es.addEventListener('job', (e) => watched?.onJob(JSON.parse(e.data)))
  es.addEventListener('log', (e) => watched?.onLog(JSON.parse(e.data).text))
  es.addEventListener('bye', () => { es.close(); setTimeout(openStream, 200) })
  es.onerror = () => { es.close(); es = null; setTimeout(() => { if (!es && state.auth?.user) openStream() }, 3000) }
}
export function startEvents() { if (!es) openStream() }
export function stopEvents() { es?.close(); es = null; lastRev = null; clearQueries() }
export function watchJob(id, onJob, onLog) { watched = { id, onJob, onLog }; openStream() }
export function unwatchJob(id) { if (watched?.id === id) { watched = null; openStream() } }

// What the signed-in user may do somewhere in the inventory. The server decides
// per object; this only hides buttons that can never work.
export function can(type, action) {
  return Boolean(state.meta?.permissions?.[type]?.includes(action))
}

export const me = () => state.auth?.user

let timer
export function toast(text, kind = 'ok') {
  state.toast = { text, kind }
  clearTimeout(timer)
  timer = setTimeout(() => { state.toast = null }, kind === 'error' ? 5000 : 2200)
}

export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    toast(`Copied ${text.length > 60 ? text.slice(0, 57) + '…' : text}`)
  } catch {
    toast('Copy failed: the browser blocked clipboard access', 'error')
  }
}

// Called after every successful write so counts, lint and hook plans stay current.
export async function changed() {
  invalidate()
  await Promise.all([loadMeta(), refreshLint(), loadNav()])
}

export function tagColour(name) {
  return state.meta?.tags?.find((t) => t.name === name)?.colour || ''
}

export function zoneHex(code) {
  return state.meta?.zones.find((x) => x.code === code)?.hex || 'var(--c-white)'
}

export function zoneLong(code) {
  return state.meta?.zones.find((x) => x.code === code)?.long || code
}

export function ago(iso) {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  if (s < 86400 * 14) return `${Math.floor(s / 86400)} d ago`
  return new Date(iso).toLocaleDateString()
}

export const stamp = (iso) => (iso ? new Date(iso).toLocaleString() : '—')
