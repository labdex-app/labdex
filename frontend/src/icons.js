// Host icons: a fixed set (lucide, ISC licence). A host's icon is its own choice, else guessed from
// the type part of its name (srv-VM-dns-01), else from its kind.
import {
  BatteryCharging, Box, BrickWall, Cctv, Cloud, Container, Cpu, HardDrive, Laptop, Lightbulb, Monitor, Network, Printer, Router,
  Server, Smartphone, Speaker, Tv, Waypoints, Wifi,
} from 'lucide-vue-next'

export const ICONS = {
  server: Server, vm: Box, container: Container, firewall: BrickWall, switch: Network, router: Router, ap: Wifi,
  nas: HardDrive, desktop: Monitor, laptop: Laptop, phone: Smartphone, iot: Cpu, light: Lightbulb, printer: Printer,
  camera: Cctv, tv: Tv, speaker: Speaker, ups: BatteryCharging, cloud: Cloud, vip: Waypoints,
}

const BY_TYPE = {
  pm: 'server', srv: 'server', bm: 'server', vm: 'vm', ct: 'container', lxc: 'container', docker: 'container', k3s: 'container',
  fw: 'firewall', sw: 'switch', rt: 'router', ap: 'ap', wap: 'ap', nas: 'nas', san: 'nas', sto: 'nas', pc: 'desktop', ws: 'desktop',
  nb: 'laptop', lt: 'laptop', ph: 'phone', mob: 'phone', iot: 'iot', prn: 'printer', cam: 'camera', tv: 'tv', spk: 'speaker',
  ups: 'ups', pdu: 'ups', vps: 'cloud', vip: 'vip',
}

export function iconFor(host) {
  if (host?.icon && ICONS[host.icon]) return host.icon
  if (host?.kind === 'vip') return 'vip'
  const t = /^[a-z]+-([a-z0-9]+)-/.exec(host?.name || '')?.[1]
  return (t && BY_TYPE[t]) || 'server'
}

// The same icons as pictures (for the topology canvas): an SVG data URI per icon and colour.
import { createApp, h } from 'vue'
const pictures = new Map()
export function iconPicture(key, colour) {
  const k = `${key}|${colour}`
  if (!pictures.has(k)) {
    const el = document.createElement('div')
    const app = createApp({ render: () => h(ICONS[key] || ICONS.server, { size: 24, color: colour, 'stroke-width': 1.75 }) })
    app.mount(el)
    let svg = el.innerHTML
    app.unmount()
    if (!svg.includes('xmlns=')) svg = svg.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"')
    pictures.set(k, `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`)
  }
  return pictures.get(k)
}
