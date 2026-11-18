<script setup>
// The top of the Overview: four numbers that say whether the lab is fine, one row per site,
// and a short list of what needs attention, each with the one thing to do about it.
// Every action opens the page where it's done (plans are approved on Changes, never here).
import { computed } from 'vue'
import { ago, state } from '../store'

const props = defineProps({ d: { type: Object, required: true } })
const c = computed(() => props.d.counts)
const inSite = (code) => !state.site || code === state.site
const sites = computed(() => (props.d.sites || []).filter((s) => inSite(s.code)))
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0)

const monitored = computed(() => c.value.up + c.value.down)
const attention = computed(() => {
  const out = []
  for (const j of props.d.failing || []) out.push({ sev: 'err', title: `${j.action} failed`, sub: `${j.integration}${j.hosts?.length ? ` · ${j.hosts.join(', ')}` : ''}`, act: 'Open log', to: `/jobs/${j.id}` })
  if (c.value.down) out.push({ sev: 'err', title: `${c.value.down} host${c.value.down === 1 ? ' is' : 's are'} down`, sub: 'monitoring says not reachable', act: 'Show them', to: '/hosts' })
  for (const i of props.d.lint_top || []) out.push({ sev: 'warn', title: i.message, sub: `${i.kind} ${i.ref} · ${i.code}`, act: 'Open check', to: '/lint' })
  const u = c.value.discovery?.unknown_hosts
  if (u) out.push({ sev: 'info', title: `${u} unknown host${u === 1 ? '' : 's'} on the network`, sub: 'seen by discovery', act: 'Add to inventory', to: '/discovery' })
  if (c.value.updates?.security) out.push({ sev: 'warn', title: `${c.value.updates.security} security update${c.value.updates.security === 1 ? '' : 's'} waiting`, sub: `${c.value.updates.hosts} host(s)${c.value.updates.reboot ? `, ${c.value.updates.reboot} need a reboot` : ''}`, act: 'Plan patch run', to: '/patches' })
  return out.slice(0, 6)
})
const mine = computed(() => (props.d.waiting || []).filter((j) => j.can_approve).length)
const SEV = { err: 'err', warn: 'warn', info: 'info' }
</script>

<template>
  <div class="ov">
    <div class="kpis">
      <RouterLink to="/hosts" class="kpi">
        <span class="label">Hosts online</span>
        <b><template v-if="monitored">{{ c.up }}<span class="of">/{{ monitored }}</span></template><template v-else>{{ c.hosts }}</template></b>
        <div class="bar"><div :style="{ width: `${pct(c.up, monitored)}%`, background: 'var(--ok)' }"></div></div>
        <span class="small muted">{{ !monitored ? 'no monitoring yet' : c.down ? `${c.down} offline` : 'all reachable' }}</span>
      </RouterLink>
      <RouterLink to="/lint" class="kpi">
        <span class="label">Needs attention</span>
        <b :class="{ 'c-red': attention.length }">{{ attention.length }}</b>
        <div class="bar"><div :style="{ width: `${Math.min(100, attention.length * 20)}%`, background: 'var(--err)' }"></div></div>
        <span class="small muted">{{ c.lint.error || 0 }} error{{ c.lint.error === 1 ? '' : 's' }}, {{ c.lint.warning || 0 }} warning{{ c.lint.warning === 1 ? '' : 's' }}</span>
      </RouterLink>
      <RouterLink to="/patches" class="kpi">
        <span class="label">Updates waiting</span>
        <b>{{ c.updates?.hosts || 0 }}</b>
        <div class="bar"><div :style="{ width: `${pct(c.updates?.hosts || 0, c.hosts)}%`, background: 'var(--warn)' }"></div></div>
        <span class="small muted">{{ c.updates?.security || 0 }} security, {{ c.updates?.reboot || 0 }} reboot{{ c.updates?.reboot === 1 ? '' : 's' }}</span>
      </RouterLink>
      <RouterLink to="/changes" class="kpi">
        <span class="label">Changes to review</span>
        <b :class="{ amber: d.waiting.length }">{{ d.waiting.length }}</b>
        <div class="bar"><div :style="{ width: `${Math.min(100, d.waiting.length * 25)}%`, background: 'var(--accent)' }"></div></div>
        <span class="small muted">{{ mine ? `${mine} need${mine === 1 ? 's' : ''} your go-ahead` : d.scheduled.length ? `${d.scheduled.length} scheduled` : 'nothing waiting' }}</span>
      </RouterLink>
    </div>

    <section class="card flush mt-4">
      <div class="sect-head pad"><h2>Sites</h2></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Site</th><th>Source</th><th>Hosts</th><th>Status</th><th>Updates</th><th>Drift</th><th>Last apply</th></tr></thead>
          <tbody>
            <tr v-for="s in sites" :key="s.code">
              <td><b>{{ s.code }}</b></td>
              <td><span v-if="s.git" class="h-tag" :class="{ bad: s.git.error }" :title="s.git.error ? 'the last sync failed' : `synced ${ago(s.git.synced_at)}`">{{ s.git.branch }} · {{ s.git.commit || '—' }}</span>
                <span v-else class="faint small">no git source</span></td>
              <td class="mono">{{ s.hosts }}</td>
              <td><span class="dot" :class="!s.monitored ? 'unknown' : s.down ? 'warn' : 'ok'"></span>
                {{ !s.monitored ? 'not monitored' : s.down ? `${s.down} offline` : 'all online' }}</td>
              <td class="mono">{{ s.updates }}</td>
              <td><span class="dot" :class="s.open_plans ? 'drift' : 'ok'"></span> {{ s.open_plans ? `${s.open_plans} planned` : 'in sync' }}</td>
              <td class="mono small">{{ s.last_apply ? ago(s.last_apply) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div class="two mt-4">
      <section class="card flush">
        <div class="sect-head pad"><h2>Needs attention</h2></div>
        <ul v-if="attention.length" class="att">
          <li v-for="(a, n) in attention" :key="n" :class="SEV[a.sev]">
            <div class="grow"><div>{{ a.title }}</div><div class="small muted">{{ a.sub }}</div></div>
            <RouterLink :to="a.to" class="act">{{ a.act }}</RouterLink>
          </li>
        </ul>
        <p v-else class="empty-line pad">Nothing needs you. ✓</p>
      </section>
      <section class="card flush">
        <div class="sect-head pad"><h2>Activity</h2><span class="grow"></span><RouterLink to="/history" class="small">All</RouterLink></div>
        <ul v-if="d.changes.length" class="att">
          <li v-for="e in d.changes.slice(0, 5)" :key="e.id">
            <span class="dot" :class="e.action === 'delete' ? 'err' : e.action === 'apply' ? 'ok' : 'info'" style="margin-top: 7px"></span>
            <div class="grow"><RouterLink :to="`/history/${e.id}`" class="plain">{{ e.summary || `${e.action} ${e.object_type} ${e.object_name}` }}</RouterLink>
              <div class="small muted">{{ e.actor }}</div></div>
            <span class="mono small faint">{{ ago(e.ts).replace(' ago', '') }}</span>
          </li>
        </ul>
        <p v-else class="empty-line pad">No changes yet.</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.kpi { display: flex; flex-direction: column; gap: 6px; padding: 16px 18px; background: var(--card); border: 1px solid var(--line); border-radius: var(--r-box); color: var(--ink); }
.kpi:hover { border-color: var(--line-strong); text-decoration: none; }
.kpi b { font: 500 calc(28px * var(--text))/1.15 var(--mono); }
.kpi b .of { color: var(--faint); }
.kpi b.amber { color: var(--accent-soft); }
.kpi .bar { min-width: 0; height: 4px; }
.pad { padding: 14px 18px; margin: 0; }
.h-tag.bad { color: var(--err); border-color: var(--err); }
.two { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 14px; align-items: start; }
.two > .card { margin-top: 0; }
.att { list-style: none; margin: 0; padding: 0; }
.att li { display: flex; gap: 12px; align-items: flex-start; padding: 11px 18px; border-top: 1px solid var(--line); }
.att li.err { box-shadow: inset 3px 0 0 var(--err); } .att li.warn { box-shadow: inset 3px 0 0 var(--warn); } .att li.info { box-shadow: inset 3px 0 0 var(--info); }
.act { font-weight: 500; white-space: nowrap; }
a.plain { color: var(--ink); }
@media (max-width: 1000px) { .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } .two { grid-template-columns: minmax(0, 1fr); } }
</style>
