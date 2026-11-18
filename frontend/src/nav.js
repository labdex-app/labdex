// The sidebar's map of the app: five groups, fifteen entries at most. An entry can stand for
// several routes (Physical is Racks, Cables and Labels); those show as tabs under the top bar
// until each area gets its own merged page. Routes outside the map stay one Ctrl K away.
import {
  Activity, Boxes, Container, GitPullRequestArrow, Globe, Inbox, LayoutGrid, Network, PackageCheck, Plug, Server, Shield,
  SquareStack, Waypoints, Wrench,
} from 'lucide-vue-next'

export const SECTIONS = [
  { id: 'home', label: '', items: [
    { to: '/', label: 'Overview', icon: LayoutGrid, exact: true },
    { to: '/lint', label: 'Inbox', icon: Inbox, badge: 'inbox',
      tabs: [{ to: '/lint', label: 'Checks' }, { to: '/discovery', label: 'Discovery', show: 'discovery' }] },
  ] },
  { id: 'inventory', label: 'Inventory', items: [
    { to: '/hosts', label: 'Hosts', icon: Server, count: 'hosts' },
    { to: '/networks', label: 'Networks', icon: Network, count: 'networks',
      tabs: [{ to: '/networks', label: 'Networks' }, { to: '/zones', label: 'Zones' }] },
    { to: '/services', label: 'Services', icon: SquareStack },
    { to: '/racks', label: 'Physical', icon: Boxes,
      tabs: [{ to: '/racks', label: 'Racks' }, { to: '/cables', label: 'Cables' }, { to: '/labels', label: 'Labels' }] },
  ] },
  { id: 'network', label: 'Network', items: [
    { to: '/topology', label: 'Topology', icon: Waypoints,
      tabs: [{ to: '/topology', label: 'Map' }, { to: '/impact', label: 'What breaks?' }] },
    { to: '/policy', label: 'Firewall', icon: Shield,
      tabs: [{ to: '/policy', label: 'Zone policy' }, { to: '/aliases', label: 'Aliases' }] },
    { to: '/dns', label: 'DNS', icon: Globe },
  ] },
  { id: 'operate', label: 'Operate', items: [
    { to: '/patches', label: 'Updates', icon: PackageCheck },
    { to: '/features', label: 'Automation', icon: Wrench },
    { to: '/workloads', label: 'Workloads', icon: Container },
  ] },
  { id: 'change', label: 'Change', items: [
    { to: '/changes', label: 'Changes', icon: GitPullRequestArrow, badge: 'pending', also: ['/jobs'],
      tabs: [{ to: '/changes', label: 'Plans' }, { to: '/changesets', label: 'Change sets' }] },
    { to: '/history', label: 'Activity', icon: Activity },
    { to: '/plugins', label: 'Integrations', icon: Plug, show: 'integrations', also: ['/integrations'] },
  ] },
]

const under = (path, to) => path === to || path.startsWith(`${to}/`)

// the sidebar entry a path belongs to: /hosts/web-01 is Hosts, /cables is Physical
export function entryFor(path) {
  for (const s of SECTIONS) {
    for (const i of s.items) {
      if (i.exact ? path === i.to : under(path, i.to)) return i
      if (i.tabs?.some((t) => under(path, t.to)) || i.also?.some((a) => under(path, a))) return i
    }
  }
  return null
}
