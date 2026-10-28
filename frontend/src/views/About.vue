<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'
import TrustBadge from '../components/TrustBadge.vue'
import { invalidate, useQuery } from '../query'
import { state, toast } from '../store'

const { data } = useQuery('/api/about', { onError: (e) => toast(e.message, 'error') })

// Check now: the server asks again past its cache; the page then reloads with the answer.
const checking = ref(false)
async function checkNow() {
  checking.value = true
  try {
    const u = await api.post('/api/about/update-check')
    toast(u.newer ? `${u.latest.tag} is out` : u.latest ? `Up to date (latest: ${u.latest.tag})` : `No answer: ${u.error || 'unknown'}`, u.latest ? 'ok' : 'error')
    invalidate('/api/about')
  } catch (e) { toast(e.message, 'error') } finally { checking.value = false }
}
const checkedAt = computed(() => {
  const t = data.value?.update_check?.checked
  return t ? new Date(t).toLocaleString() : ''
})

// The changelog is our own Markdown: headings, lists, paragraphs, `code`, **bold**, [links](url).
// Escaped first, so nothing in it can become HTML.
const esc = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const inline = (t) => esc(t)
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
function render(md) {
  const out = []
  let list = null
  let para = []
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = [] } }
  const flushList = () => { if (list) { out.push(`<ul>${list.map((i) => `<li>${inline(i)}</li>`).join('')}</ul>`); list = null } }
  for (const raw of md.split('\n')) {
    const line = raw.trimEnd()
    const h = /^(#{1,3})\s+(.*)$/.exec(line)
    if (h) { flushPara(); flushList(); out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); continue }
    const li = /^\s*[-*]\s+(.*)$/.exec(line)
    if (li) { flushPara(); (list ||= []).push(li[1]); continue }
    if (!line.trim()) { flushPara(); flushList(); continue }
    if (list && /^\s{2,}/.test(raw)) { list[list.length - 1] += ' ' + line.trim(); continue }
    flushList(); para.push(line.trim())
  }
  flushPara(); flushList()
  return out.join('\n')
}
const changelog = computed(() => render((data.value?.changelog || '').replace(/^# Changelog\s*/, '')))
</script>

<template>
  <div class="page">
    <div class="page-head"><h1>About labdex</h1><span class="sub">{{ data?.version || state.meta.version }}</span></div>
    <div class="warnbox small" style="margin-bottom: 16px"><b>Built with AI, for home labs only.</b> labdex was written largely with an AI coding
      assistant (Claude), directed and reviewed by a human. It hasn't had an independent security audit and may contain security problems:
      run it on a trusted home network, not on the internet or for production.</div>
    <div v-if="data" class="split">
      <div class="card changelog">
        <h2>Changelog</h2>
        <!-- eslint-disable-next-line vue/no-v-html -- render() escapes everything first and only adds its own tags -->
        <div v-if="data.changelog" v-html="changelog"></div>
        <p v-else class="faint">This build doesn't include the changelog.</p>
      </div>
      <aside>
        <div v-if="data.update_check" class="card">
          <h3>Updates</h3>
          <p v-if="!data.update_check.latest" class="faint" style="margin: 0">No answer from <a :href="data.update_check.project" target="_blank" rel="noopener">{{ data.update_check.project.replace('https://', '') }}</a>: {{ data.update_check.error || 'unknown' }}.</p>
          <p v-else-if="data.update_check.newer" style="margin: 0">
            <strong>{{ data.update_check.latest.tag }}</strong> is out.
            <a :href="data.update_check.latest.url" target="_blank" rel="noopener">Release notes ↗</a>
          </p>
          <p v-else style="margin: 0">Up to date (latest: {{ data.update_check.latest.tag }}).</p>
          <div class="check-row">
            <span v-if="checkedAt" class="faint small">Checked {{ checkedAt }}</span>
            <button class="btn small" :disabled="checking" @click="checkNow">{{ checking ? 'Checking…' : 'Check now' }}</button>
          </div>
        </div>
        <div class="card">
          <h3>This instance</h3>
          <dl class="kv">
            <dt>Version</dt><dd class="mono">{{ data.version }}</dd>
            <dt>Schema</dt><dd class="mono">{{ data.schema.current }}<span v-if="data.schema.current !== data.schema.newest" class="c-yellow"> (newest {{ data.schema.newest }})</span></dd>
            <dt>Database</dt><dd><span class="mono">{{ data.database.backend }}</span> <span v-if="data.database.where" class="faint small">{{ data.database.where }}</span></dd>
            <dt>Python</dt><dd class="mono">{{ data.python }}</dd>
            <dt>Platform</dt><dd class="small">{{ data.platform }}</dd>
            <dt>Hosts</dt><dd>{{ data.counts.hosts }}</dd>
            <dt>Plugin instances</dt><dd>{{ data.counts.integrations }}</dd>
            <dt>Jobs</dt>
            <dd>
              <template v-if="data.jobs.mode === 'inline'">run by the web server</template>
              <template v-else>run by <code>labdex worker</code></template>
              <span v-if="data.jobs.waiting" class="faint small"> · {{ data.jobs.waiting }} waiting</span>
              <div v-if="!data.jobs.ok" class="c-yellow small">No worker seen in the last minute: queued jobs wait until <code>labdex worker</code> runs.</div>
              <div v-for="w in data.jobs.workers" :key="w.id || w.mode" class="small faint">
                <span class="mono">{{ w.id || w.mode }}</span> · {{ w.running }} running<template v-if="w.version"> · {{ w.version }}</template></div>
            </dd>
          </dl>
        </div>
        <div class="card">
          <h3>Plugins</h3>
          <table>
            <tbody>
              <tr v-for="p in data.plugins" :key="p.id">
                <td>{{ p.name }} <div class="small faint mono">{{ p.id }}</div></td>
                <td class="mono small">{{ p.version || '—' }}</td>
                <td><TrustBadge :trust="{ status: p.trust }" /><span v-if="p.error" class="c-red small"> error</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.check-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 10px; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; margin: 0; }
.kv dt { color: var(--faint); }
.kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.changelog :deep(h3) { margin: 26px 0 8px; font-size: calc(18px * var(--text)); text-transform: none; letter-spacing: 0; color: var(--ink); font-family: var(--mono); }
.changelog :deep(h4) { margin: 14px 0 4px; font-size: calc(14px * var(--text)); text-transform: none; letter-spacing: 0; color: var(--muted); }
.changelog :deep(ul) { margin: 4px 0 8px; padding-left: 20px; }
.changelog :deep(li) { margin: 3px 0; line-height: 1.5; }
.changelog :deep(p) { line-height: 1.55; }
</style>
