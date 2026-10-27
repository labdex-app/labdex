// A small stale-while-revalidate cache for GET requests.
//
// - useQuery(path) shows the cached answer at once and refreshes it in the background
//   when it is older than staleMs or was invalidated.
// - The server announces every change on the event stream ('revision'); that, and every
//   write made here (changed() in store.js), invalidates the cache. Pages on screen refetch
//   at once; pages kept alive by <KeepAlive> refetch when they're shown again.
// - The browser revalidates with the ETag, so an unchanged answer costs an empty 304.
// - Answers are kept in shallowRefs: rows are replaced, never deeply watched.
import { getCurrentInstance, onActivated, onDeactivated, onUnmounted, ref, shallowRef, toValue, watch } from 'vue'
import { api } from './api'

const entries = new Map() // path -> { data, at, stale, promise }
const active = new Set() // { path, refetch } of mounted, visible queries
const MAX_ENTRIES = 200

function entry(path) {
  let e = entries.get(path)
  if (!e) {
    e = { data: undefined, at: 0, stale: true, promise: null }
    entries.set(path, e)
    if (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value)
  }
  return e
}

export function fetchQuery(path) {
  const e = entry(path)
  if (e.promise) return e.promise
  e.promise = api.get(path)
    .then((data) => { e.data = data; e.at = Date.now(); e.stale = false; return data })
    .finally(() => { e.promise = null })
  return e.promise
}

export function prefetch(path) {
  const e = entries.get(path)
  if (!e || e.stale || Date.now() - e.at > 10000) fetchQuery(path).catch(() => {})
}

// Put a fresh answer in the cache after a write (e.g. the host the PUT returned).
export function setQueryData(path, data) {
  const e = entry(path)
  e.data = data; e.at = Date.now(); e.stale = false
}

// Mark everything under prefix stale; queries on screen refetch now.
export function invalidate(prefix = '/api/') {
  for (const [path, e] of entries) if (path.startsWith(prefix)) e.stale = true
  for (const q of active) if (q.path?.startsWith(prefix)) q.refetch()
}

export function useQuery(source, { staleMs = 30000, onError } = {}) {
  const data = shallowRef(null)
  const error = shallowRef(null)
  const loading = ref(false)
  const me = { path: null, refetch: () => load(true) }
  let visible = true

  async function load(background = false) {
    const path = me.path
    if (!path) return
    const e = entry(path)
    if (e.data !== undefined) data.value = e.data
    const fresh = !e.stale && Date.now() - e.at < staleMs
    if (fresh && e.data !== undefined) return
    loading.value = !background || e.data === undefined
    try {
      const d = await fetchQuery(path)
      if (me.path === path) { data.value = d; error.value = null }
    } catch (err) {
      if (me.path === path) { error.value = err; onError?.(err) }
    } finally {
      if (me.path === path) loading.value = false
    }
  }

  watch(() => toValue(source), (path) => {
    me.path = path || null
    if (!path) { data.value = null; return }
    const cached = entries.get(path)?.data
    data.value = cached === undefined ? null : cached
    load(cached !== undefined)
  }, { immediate: true })

  if (getCurrentInstance()) {
    active.add(me)
    onDeactivated(() => { visible = false; active.delete(me) })
    onActivated(() => { if (!visible) { visible = true; active.add(me); load(true) } })
    onUnmounted(() => active.delete(me))
  }

  return { data, error, loading, refresh: () => { if (me.path) entry(me.path).stale = true; return load(true) } }
}

// The cached answer when it's fresh enough, otherwise a fetch (used for one-off loads, e.g. a form).
export function getCached(path, maxAge = 10000) {
  const e = entries.get(path)
  if (e && e.data !== undefined && !e.stale && Date.now() - e.at < maxAge) return Promise.resolve(e.data)
  return fetchQuery(path)
}

// On sign-out or when the session ends: nothing of the previous user stays in memory.
export function clearQueries() { entries.clear() }
