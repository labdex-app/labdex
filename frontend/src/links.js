// Cables: media, speeds and the colour a speed gets on the topology (and in lists).
export const MEDIA = ['copper', 'fiber', 'dac', 'wifi']
export const SPEEDS = ['100M', '1G', '2.5G', '5G', '10G', '25G', '40G', '100G', '400G']

/** "2.5G", "10 Gbit", "100M", "1000" → megabits per second (0 when it can't tell). */
export function speedMbps(s) {
  const m = /^\s*([\d.]+)\s*([kmgt]?)/i.exec(String(s || ''))
  if (!m) return 0
  const n = parseFloat(m[1])
  const unit = m[2].toLowerCase()
  return unit === 't' ? n * 1e6 : unit === 'g' ? n * 1000 : unit === 'k' ? n / 1000 : n
}

// colour-blind friendly steps, light enough on dark and dark enough on light
export const SPEED_STEPS = [
  { min: 0, label: 'under 1G', colour: '#9ca3af' },
  { min: 1000, label: '1G', colour: '#3b82f6' },
  { min: 2500, label: '2.5–5G', colour: '#06b6d4' },
  { min: 10000, label: '10G', colour: '#22c55e' },
  { min: 25000, label: '25G', colour: '#f59e0b' },
  { min: 40000, label: '40G+', colour: '#e11d48' },
]
export function speedStep(s) {
  const v = speedMbps(s)
  if (!v) return null
  return [...SPEED_STEPS].reverse().find((x) => v >= x.min)
}

// the jacket colours patch cables come in (the name is stored; any other text or #rrggbb works too)
export const CABLE_COLOURS = [
  { name: 'blue', hex: '#3b82f6' }, { name: 'grey', hex: '#9ca3af' }, { name: 'yellow', hex: '#facc15' }, { name: 'green', hex: '#22c55e' },
  { name: 'red', hex: '#ef4444' }, { name: 'orange', hex: '#f97316' }, { name: 'purple', hex: '#a855f7' }, { name: 'white', hex: '#f5f5f5' },
  { name: 'black', hex: '#262626' }, { name: 'pink', hex: '#ec4899' }, { name: 'aqua', hex: '#22d3ee' },
]

/** A cable colour (a name from the list, or #hex) as #hex; '' when it has none. */
export function cableHex(c) {
  if (!c) return ''
  if (/^#[0-9a-f]{3,8}$/i.test(c)) return c
  return CABLE_COLOURS.find((x) => x.name === String(c).toLowerCase())?.hex || ''
}

// Interfaces: what a physical port is, and which kinds are physical at all (the rest sit on something).
export const CONNECTORS = ['rj45', 'sfp', 'sfp+', 'sfp28', 'qsfp+', 'qsfp28', 'qsfp-dd', 'usb', 'console', 'radio', 'other']
// the speed a connector usually runs at, offered when one is picked
export const CONNECTOR_SPEED = { rj45: '1G', sfp: '1G', 'sfp+': '10G', sfp28: '25G', 'qsfp+': '40G', qsfp28: '100G', 'qsfp-dd': '400G' }
/** Sets the connector and, if the speed was empty or the old connector's usual one, the new usual speed. */
export function setConnector(i, c) {
  if (!i.speed || i.speed === CONNECTOR_SPEED[i.connector]) i.speed = CONNECTOR_SPEED[c] || i.speed
  i.connector = c
}
export const PHYSICAL_KINDS = ['', 'ethernet', 'wifi']
export const isPhysical = (i) => PHYSICAL_KINDS.includes(i.kind || '')

/** Names from a pattern with ranges in brackets: "eth[0-3]" → eth0…eth3, "1/0/[1-48]",
 *  "swp[1-4]s[0-3]" (every combination), "Gi1/0/[01-24]" keeps the leading zero. At most `max`. */
export function expandRange(pattern, max = 256) {
  const src = String(pattern || '').trim()
  if (!src) return []
  const parts = src.split(/(\[\d+\s*-\s*\d+\])/)
  let out = ['']
  for (const p of parts) {
    const m = /^\[(\d+)\s*-\s*(\d+)\]$/.exec(p)
    if (!m) { out = out.map((x) => x + p); continue }
    const [a, b] = [parseInt(m[1], 10), parseInt(m[2], 10)]
    const width = m[1].length > 1 && m[1].startsWith('0') ? m[1].length : 0
    const step = a <= b ? 1 : -1
    const nums = []
    for (let n = a; step > 0 ? n <= b : n >= b; n += step) { nums.push(String(n).padStart(width, '0')); if (nums.length > max) break }
    out = out.flatMap((x) => nums.map((n) => x + n))
    if (out.length > max) return out.slice(0, max + 1)
  }
  return out
}
