<script setup>
// Where to look and whom to ask: the docs, the project, keyboard shortcuts, and what to put
// in a bug report (copied in one click).
import { BookOpen, Bug, Copy as CopyIcon, Keyboard, LifeBuoy, MessagesSquare, ShieldAlert } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { state, toast } from '../store'

const about = ref(null)
onMounted(() => api.get('/api/about').then((d) => { about.value = d }).catch(() => {}))
const project = computed(() => state.meta?.project_url || 'https://github.com/reykimedev/labdex')
const host = computed(() => { try { return new URL(project.value).host } catch { return '' } })
const isGitlab = computed(() => host.value.includes('gitlab'))
const docs = computed(() => {
  const m = project.value.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+)$/)
  return m ? `https://${m[1]}.github.io/${m[2]}/` : `${project.value}#readme`
})
const issues = computed(() => (isGitlab.value ? `${project.value}/-/issues` : `${project.value}/issues`))
const isMac = navigator.platform?.toLowerCase().includes('mac')
const mod = isMac ? '⌘' : 'Ctrl'
const KEYS = [
  [[`${mod} K`, '/'], 'Search and go: hosts, addresses, networks, pages'],
  [[`${mod} B`], 'Collapse or expand the sidebar'],
  [['Enter'], 'Open the row that has the focus (lists)'],
  [['Esc'], 'Close a dialog or a menu'],
]
const report = computed(() => {
  const a = about.value
  return [`labdex ${a?.version || state.meta?.version || ''}`, a && `schema ${a.schema.current}`, a && `database ${a.database.backend}`,
    a && `python ${a.python}`, a && a.platform, `browser ${navigator.userAgent}`].filter(Boolean).join('\n')
})
async function copyReport() {
  try { await navigator.clipboard.writeText(report.value); toast('Copied') } catch { toast('Copy it from the box', 'error') }
}
</script>

<template>
  <div class="page">
    <div class="page-head"><h1>Help</h1><span class="sub">Where to read up, and how to ask</span></div>
    <div class="hgrid">
      <a :href="docs" target="_blank" rel="noopener" class="card hcard">
        <BookOpen :size="20" /><div><b>Documentation</b><p>Installing, the inventory, zones and policy, plugins, backups, the API.</p></div></a>
      <a :href="issues" target="_blank" rel="noopener" class="card hcard">
        <Bug :size="20" /><div><b>Report a problem or ask</b><p>Open an issue on {{ host || 'the project' }}. Paste the details below so it's clear what you run.</p></div></a>
      <a :href="project" target="_blank" rel="noopener" class="card hcard">
        <MessagesSquare :size="20" /><div><b>The project</b><p>Source, releases, the changelog and the plugins: <span class="mono small">{{ project.replace('https://', '') }}</span></p></div></a>
      <a :href="`${docs}operate/security/`" target="_blank" rel="noopener" class="card hcard">
        <ShieldAlert :size="20" /><div><b>A security problem?</b><p>Please don't open a public issue: the security page says how to report it privately.</p></div></a>
    </div>

    <div class="split2">
      <section class="card">
        <h2><Keyboard :size="17" /> Keyboard</h2>
        <dl class="keys">
          <template v-for="[ks, what] in KEYS" :key="what"><dt><template v-for="(k, n) in ks" :key="k"><span v-if="n" class="faint small"> or </span><kbd>{{ k }}</kbd></template></dt><dd>{{ what }}</dd></template>
        </dl>
      </section>
      <section class="card">
        <h2><LifeBuoy :size="17" /> For a bug report</h2>
        <pre class="rep">{{ report }}</pre>
        <button class="btn small" @click="copyReport"><CopyIcon :size="14" /> Copy</button>
        <p class="small faint" style="margin-bottom: 0">Nothing is sent anywhere; this only goes where you paste it.</p>
      </section>
    </div>
    <p class="small muted">labdex was built with an AI coding assistant and is meant for home labs: keep it on a trusted network.</p>
  </div>
</template>

<style scoped>
.hgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; margin-bottom: 18px; }
.hcard { display: flex; gap: 12px; margin: 0; color: var(--ink); align-items: flex-start; }
.hcard:hover { text-decoration: none; border-color: var(--accent); }
.hcard > svg { color: var(--accent-soft); flex: none; margin-top: 2px; }
.hcard p { margin: 4px 0 0; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.split2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; margin-bottom: 14px; }
.split2 .card { margin: 0; }
.split2 h2 { display: flex; align-items: center; gap: 8px; }
.keys { display: grid; grid-template-columns: max-content 1fr; gap: 8px 14px; margin: 0; }
.keys dd { margin: 0; color: var(--muted); }
kbd { font-family: var(--mono); font-size: 12px; border: 1px solid var(--line-strong); border-bottom-width: 2px; border-radius: 5px; padding: 1px 6px; background: var(--raise); }
.rep { background: var(--raise); border-radius: var(--r-ctl); padding: 10px 12px; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0 0 10px; }
</style>
