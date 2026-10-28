// Label formats and what goes on a label, for the Labels page. Sizes in millimetres.
//
// A roll (label printer) prints one label per page at its own size; a sheet (A4) places
// them on a grid. A flag wraps around a cable: the same text on both halves.

export const FORMATS = [
  { id: 'brother-62x29', name: 'Brother DK 62 × 29 mm', w: 62, h: 29 },
  { id: 'brother-54x17', name: 'Brother DK-11204 54 × 17 mm', w: 54, h: 17 },
  { id: 'dymo-89x36', name: 'Dymo 99012 89 × 36 mm', w: 89, h: 36 },
  { id: 'dymo-54x25', name: 'Dymo 11352 54 × 25 mm', w: 54, h: 25 },
  { id: 'tape-12', name: '12 mm tape, 72 mm flag around a cable', w: 72, h: 12, flag: true },
  { id: 'avery-l7651', name: 'A4 sheet 38.1 × 21.2 mm, 65 per page (Avery L7651)', w: 38.1, h: 21.2,
    sheet: { cols: 5, rows: 13, top: 10.7, left: 4.75, gapX: 2.5, gapY: 0 } },
  { id: 'avery-3474', name: 'A4 sheet 70 × 37 mm, 24 per page (Avery 3474)', w: 70, h: 37,
    sheet: { cols: 3, rows: 8, top: 0.5, left: 0, gapX: 0, gapY: 0 } },
]
export const formatById = (id) => FORMATS.find((f) => f.id === id) || FORMATS[0]

// below this height a QR code is too small to scan from a phone
export const QR_MIN_MM = 14

const primaryIp = (h) => {
  const ifs = h.interfaces || []
  const i = ifs.find((x) => x.primary && x.ip4) || ifs.find((x) => x.ip4)
  return i ? i.ip4 : ''
}

// {key, title, sub, lines, url, colour}: title big, sub mono, lines small, url for the QR code
export function hostLabel(h, base) {
  return { key: `h:${h.name}`, title: h.fun || h.name, sub: h.fun ? h.name : '', lines: [primaryIp(h), h.zone].filter(Boolean),
    url: `${base}/hosts/${h.uid || h.name}` }
}

export function rackLabel(r, base) {
  return { key: `r:${r.uid || r.name}`, title: r.name, sub: r.site || '', lines: [r.units ? `${r.units} U` : ''].filter(Boolean),
    url: `${base}/racks?rack=${r.uid || ''}` }
}

// two per cable: one for each end, each saying which end it's on and where the other goes
export function cableLabels(c, base) {
  const ends = [['a', 'b'], ['b', 'a']]
  return ends.map(([me, other]) => ({
    key: `c:${c.id}:${me}`, title: c.label || `${c.a_host} ⇄ ${c.b_host}`, colour: c.colour || '',
    sub: `${c[`${me}_host`]} ${c[`${me}_port`]}`, lines: [`→ ${c[`${other}_host`]} ${c[`${other}_port`]}`,
      [c.medium, c.speed, c.length ? `${c.length} m` : ''].filter(Boolean).join(' · ')].filter(Boolean),
    url: `${base}/cables?cable=${c.id}`,
  }))
}

// a switch's or patch panel's ports: one label per cabled port, naming the far end
export function portLabels(hostName, cables, base) {
  const out = []
  for (const c of cables) {
    for (const [me, other] of [['a', 'b'], ['b', 'a']]) {
      if (c[`${me}_host`] !== hostName) continue
      out.push({ key: `p:${hostName}:${c[`${me}_port`]}`, title: c[`${me}_port`], sub: `→ ${c[`${other}_host`]} ${c[`${other}_port`]}`,
        lines: [c.label].filter(Boolean), colour: c.colour || '', url: `${base}/cables?cable=${c.id}`, port: c[`${me}_port`] })
    }
  }
  const natural = (a, b) => a.port.localeCompare(b.port, undefined, { numeric: true })
  return out.sort(natural)
}

// where a sheet's label n (0 = top left) goes, in mm
export function sheetSlot(f, n) {
  const s = f.sheet
  const per = s.cols * s.rows
  const i = n % per
  const col = i % s.cols
  const row = Math.floor(i / s.cols)
  return { page: Math.floor(n / per), x: s.left + col * (f.w + s.gapX), y: s.top + row * (f.h + s.gapY) }
}
