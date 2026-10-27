import { createRouter, createWebHistory } from 'vue-router'
import Hosts from './views/Hosts.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/Dashboard.vue'), meta: { title: 'Dashboard' } },
    { path: '/hosts', component: Hosts, meta: { title: 'Hosts' } },
    { path: '/hosts/:name', component: () => import('./views/HostEdit.vue'), props: true, meta: { title: 'Host' } },
    { path: '/admin/secrets', redirect: '/vault' }, // 1.18: the global secrets live in the vault (global/…)
    { path: '/vault', component: () => import('./views/Vault.vue'), meta: { title: 'Vault' } },
    { path: '/cables', component: () => import('./views/Cables.vue'), meta: { title: 'Cables' } },
    { path: '/racks', component: () => import('./views/Racks.vue'), meta: { title: 'Racks' } },
    { path: '/networks', component: () => import('./views/Networks.vue'), meta: { title: 'Networks' } },
    { path: '/networks/:name', component: () => import('./views/NetworkEdit.vue'), props: true, meta: { title: 'Network' } },
    { path: '/aliases', component: () => import('./views/Aliases.vue'), meta: { title: 'Aliases' } },
    { path: '/services', component: () => import('./views/Services.vue'), meta: { title: 'Services' } },
    { path: '/dns', component: () => import('./views/Dns.vue'), meta: { title: 'DNS' } },
    { path: '/discovery', component: () => import('./views/Discovery.vue'), meta: { title: 'Discovery' } },
    { path: '/plugins', component: () => import('./views/Plugins.vue'), meta: { title: 'Plugins' } },
    { path: '/plugins/:id', component: () => import('./views/Plugin.vue'), props: true, meta: { title: 'Plugin' } },
    { path: '/integrations', redirect: '/plugins' },
    { path: '/integrations/:name', component: () => import('./views/Integration.vue'), props: true, meta: { title: 'Integration' } },
    { path: '/jobs/:id', component: () => import('./views/Job.vue'), props: true, meta: { title: 'Job' } },
    { path: '/features', component: () => import('./views/Features.vue'), meta: { title: 'Features' } },
    { path: '/m/:integration/:page', redirect: (to) => ({ path: `/integrations/${to.params.integration}`, query: { tab: to.params.page } }) }, // old links
    { path: '/history', component: () => import('./views/History.vue'), meta: { title: 'History' } },
    { path: '/history/:id', component: () => import('./views/HistoryEvent.vue'), props: true, meta: { title: 'Change' } },
    { path: '/lint', component: () => import('./views/Lint.vue'), meta: { title: 'Lint' } },
    { path: '/account', component: () => import('./views/Account.vue'), meta: { title: 'Account' } },
    { path: '/topology', component: () => import('./views/Topology.vue'), meta: { title: 'Topology' } },
    { path: '/zones', component: () => import('./views/Zones.vue'), meta: { title: 'Zones' } },
    { path: '/zones/:id', component: () => import('./views/ZoneView.vue'), props: true, meta: { title: 'Zone' } },
    { path: '/policy', component: () => import('./views/Policy.vue'), meta: { title: 'Zone policy' } },
    { path: '/naming', component: () => import('./views/Naming.vue'), meta: { title: 'Naming' } },
    { path: '/about', component: () => import('./views/About.vue'), meta: { title: 'About' } },
    { path: '/admin', redirect: '/admin/general' },
    { path: '/admin/zones', redirect: '/zones' },
    { path: '/admin/naming', redirect: '/naming' },
    { path: '/admin/conventions', redirect: '/admin/general' },
    { path: '/admin/:tab', component: () => import('./views/Admin.vue'), props: true, meta: { title: 'Admin' } },
    { path: '/settings', redirect: '/admin/general' },
    { path: '/exports', redirect: '/plugins/exports' },
    { path: '/:rest(.*)*', redirect: '/' },
  ],
  // back/forward returns to where you were (list pages are kept alive, so the rows are there)
  scrollBehavior: (to, from, saved) => saved || (to.hash ? { el: to.hash } : { top: 0 }),
})

router.afterEach((to) => {
  const name = to.params.name && to.params.name !== 'new' ? `${to.params.name} · ` : ''
  document.title = `${name}${to.meta.title || ''} · labdex`
})
