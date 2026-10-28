// Thin fetch wrapper. The browser session cookie carries the login; the custom
// header is labdex's CSRF guard (a cross-site form can't set it).
export class ApiError extends Error {
  constructor(status, message, errors = []) {
    super(message)
    this.status = status
    this.errors = errors
  }

  // { 'interfaces[0].ip4': 'message', ... }
  get fields() {
    return Object.fromEntries(this.errors.map((e) => [e.field, e.message]))
  }
}

let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) { onUnauthorized = fn }

async function request(method, path, body, { text = false } = {}) {
  const headers = { 'X-Requested-With': 'labdex' }
  let payload
  if (body !== undefined) {
    payload = JSON.stringify(body)
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(path, { method, headers, body: payload, credentials: 'same-origin' })
  if (res.status === 204) return null
  if (!res.ok) {
    let data = {}
    try { data = await res.json() } catch { /* not JSON */ }
    if (res.status === 401 && !path.startsWith('/api/auth/')) onUnauthorized()
    throw new ApiError(res.status, data.error || `${res.status} ${res.statusText}`, data.errors || [])
  }
  return text ? res.text() : res.json()
}

export const api = {
  get: (path) => request('GET', path),
  text: (path) => request('GET', path, undefined, { text: true }),
  post: (path, body) => request('POST', path, body ?? {}),
  put: (path, body) => request('PUT', path, body),
  del: (path, body) => request('DELETE', path, body),
}

export const enc = encodeURIComponent
