<script setup>
// One card of a dashboard. The start page's cards draw from /api/dashboard (fetched once by the
// page and handed in as `d`); widgets, services and links bring their own data.
import { computed } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import JobBadge from './JobBadge.vue'
import WidgetGrid from './WidgetGrid.vue'
import ZoneChip from './ZoneChip.vue'
import { useQuery } from '../query'
import { ago, stamp } from '../store'

const props = defineProps({ item: { type: Object, required: true }, d: { type: Object, default: null } })
const cfg = computed(() => props.item.config || {})
const c = computed(() => props.d?.counts || {})
const what = (j) => `${j.integration} · ${j.action}${j.hosts?.length === 1 ? ` · ${j.hosts[0]}` : ''}`
const forYou = computed(() => (props.d?.waiting || []).filter((j) => j.can_approve).length)

const TITLES = { approvals: 'Waiting for approval', failing: 'Failing', running: 'Running', changes: 'Last changes', upcoming: 'Coming up',
  zones: 'Hosts per zone', links: 'Links', services: 'Services', note: 'Note' }
const title = computed(() => cfg.value.title || TITLES[props.item.type] || '')

// services: every service with a link, filtered by the card's tag, zone or category
const svcQ = props.item.type === 'services' ? useQuery('/api/services', { staleMs: 30000 }) : null
const services = computed(() => (svcQ?.data.value || []).filter((s) => s.url
  && (!cfg.value.tag || (s.tags || []).includes(cfg.value.tag))
  && (!cfg.value.zone || s.zone === cfg.value.zone || s.zone.startsWith(`${cfg.value.zone}_`))
  && (!cfg.value.category || s.category === cfg.value.category)))
const STATUS = { open: ['up', 'answering'], closed: ['down', 'not answering'], unknown: ['', 'not checked'] }
const external = (u) => /^https?:/.test(u)
const initial = (n) => (n || '?').trim().slice(0, 1).toUpperCase()
</script>

<template>
  <!-- the numbers -->
  <div v-if="item.type === 'counts'" class="tiles">
    <template v-if="d">
      <RouterLink to="/hosts" class="tile">
        <div class="lbl">Hosts</div>
        <div class="num">{{ c.hosts }}</div>
        <div class="small">
          <span v-if="c.up"><span class="dot up"></span>{{ c.up }} up</span>
          <span v-if="c.down" class="sp"><span class="dot down"></span>{{ c.down }} down</span>
          <span v-if="c.by_status?.planned" class="sp faint">{{ c.by_status.planned }} planned</span>
        </div>
      </RouterLink>
      <RouterLink to="/networks" class="tile"><div class="lbl">Networks</div><div class="num">{{ c.networks }}</div>
        <div class="small faint">{{ Object.keys(c.zones || {}).length }} zones in use</div></RouterLink>
      <RouterLink to="/services" class="tile"><div class="lbl">Services</div><div class="num">{{ c.services }}</div>
        <div class="small faint">on the Services page</div></RouterLink>
      <RouterLink to="/lint" class="tile">
        <div class="lbl">Lint</div>
        <div class="num">{{ (c.lint?.error || 0) + (c.lint?.warning || 0) }}</div>
        <div class="small">
          <span v-if="c.lint?.error" class="c-red">✕ {{ c.lint.error }} error{{ c.lint.error === 1 ? '' : 's' }}</span>
          <span v-if="c.lint?.warning" class="sp c-yellow">! {{ c.lint.warning }} warning{{ c.lint.warning === 1 ? '' : 's' }}</span>
          <span v-if="!c.lint?.error && !c.lint?.warning" class="c-green">✓ clean</span>
        </div>
      </RouterLink>
      <RouterLink v-if="c.discovery" to="/discovery" class="tile">
        <div class="lbl">Discovery</div>
        <div class="num">{{ c.discovery.unknown_hosts + c.discovery.undocumented }}</div>
        <div class="small faint">{{ c.discovery.unknown_hosts }} unknown hosts · {{ c.discovery.undocumented }} undocumented ports</div>
      </RouterLink>
    </template>
  </div>

  <WidgetGrid v-else-if="item.type === 'widgets'" url="/api/widgets" bare />
  <section v-else-if="item.type === 'widget'" class="card cardfill">
    <WidgetGrid url="/api/widgets" :only="{ integration: cfg.integration, widget: cfg.widget }" bare flat />
  </section>

  <section v-else class="card flush cardfill">
    <div class="ch"><h2>{{ title }}</h2>
      <span v-if="item.type === 'approvals' && forYou" class="pill info">{{ forYou }} for you</span>
      <span v-if="item.type === 'failing'" class="small faint">last 7 days</span>
      <RouterLink v-if="item.type === 'changes'" to="/history" class="small">History</RouterLink>
      <RouterLink v-if="item.type === 'services'" to="/services" class="small">Services</RouterLink>
    </div>
    <div class="body">
      <template v-if="item.type === 'approvals' && d">
        <ul v-if="d.waiting.length" class="rows">
          <li v-for="j in d.waiting" :key="j.id">
            <RouterLink :to="`/jobs/${j.id}`" class="rl"><span class="t">{{ what(j) }}</span>
              <span class="small faint">{{ j.changes }} change{{ j.changes === 1 ? '' : 's' }} · {{ j.approvals?.length || 0 }} of {{ j.needed }} · {{ ago(j.created_at) }}</span></RouterLink>
            <span v-if="j.can_approve" class="pill info">your turn</span>
          </li>
        </ul>
        <p v-else class="empty-p">Nothing waits for approval.</p>
        <template v-if="d.scheduled.length">
          <div class="ch sub"><h3>Approved, waiting for their time</h3></div>
          <ul class="rows"><li v-for="j in d.scheduled" :key="j.id">
            <RouterLink :to="`/jobs/${j.id}`" class="rl"><span class="t">{{ what(j) }}</span><span class="small faint">applies {{ stamp(j.apply_after) }}</span></RouterLink></li></ul>
        </template>
      </template>

      <template v-else-if="item.type === 'failing' && d">
        <ul v-if="d.failing.length" class="rows">
          <li v-for="j in d.failing" :key="j.id">
            <RouterLink :to="`/jobs/${j.id}`" class="rl"><span class="t"><span class="c-red">✕</span> {{ what(j) }}</span>
              <span class="small faint ell">{{ j.result?.error }} · {{ ago(j.created_at) }}</span></RouterLink>
          </li>
        </ul>
        <p v-else class="empty-p">✓ Nothing failing.</p>
      </template>

      <template v-else-if="item.type === 'running' && d">
        <ul v-if="d.running.length" class="rows">
          <li v-for="j in d.running" :key="j.id">
            <RouterLink :to="`/jobs/${j.id}`" class="rl"><span class="t">{{ what(j) }}</span><span class="small faint">{{ ago(j.created_at) }}</span></RouterLink>
            <JobBadge :status="j.status" />
          </li>
        </ul>
        <p v-else class="empty-p">Nothing runs right now.</p>
      </template>

      <template v-else-if="item.type === 'changes' && d">
        <ul class="rows">
          <li v-for="e in d.changes" :key="e.id">
            <RouterLink :to="`/history/${e.id}`" class="rl"><span class="t">{{ e.summary }}</span><span class="small faint">{{ e.actor }} · {{ ago(e.ts) }}</span></RouterLink>
          </li>
          <li v-if="!d.changes.length" class="faint small">No changes yet.</li>
        </ul>
      </template>

      <template v-else-if="item.type === 'upcoming' && d">
        <ul v-if="d.upcoming.length || d.windows.length" class="rows">
          <li v-for="u in d.upcoming" :key="u.name">
            <RouterLink :to="`/integrations/${u.integration}`" class="rl"><span class="t">{{ u.name }}</span>
              <span class="small faint">{{ u.integration }}<template v-if="u.host"> · {{ u.host }}</template> · {{ stamp(u.at) }}</span></RouterLink>
          </li>
          <li v-for="w in d.windows" :key="'w' + w.name">
            <RouterLink to="/admin/approvals" class="rl"><span class="t">Window {{ w.name }}</span>
              <span class="small" :class="w.open ? 'c-green' : 'faint'">{{ w.open ? 'open now' : `opens ${stamp(w.next_open)}` }}</span></RouterLink>
          </li>
        </ul>
        <p v-else class="empty-p">Nothing scheduled.</p>
      </template>

      <template v-else-if="item.type === 'zones' && d">
        <div class="zones">
          <RouterLink v-for="(n, z) in c.zones" :key="z" :to="`/hosts?zone=${z}`" class="zrow"><ZoneChip :zone="z" /><span class="mono small">{{ n }}</span></RouterLink>
        </div>
      </template>

      <template v-else-if="item.type === 'links'">
        <div class="lgrid" :style="{ '--cols': cfg.columns || 3 }">
          <component :is="external(l.url) ? 'a' : 'RouterLink'" v-for="(l, n) in cfg.links || []" :key="n" class="lk" :href="external(l.url) ? l.url : undefined"
                     :to="external(l.url) ? undefined : l.url" :target="external(l.url) ? '_blank' : undefined" :rel="external(l.url) ? 'noopener noreferrer' : undefined">
            <span class="mono-i" aria-hidden="true">{{ initial(l.name) }}</span>
            <span class="lt"><span class="ln">{{ l.name }}</span><span v-if="l.description" class="small faint ell">{{ l.description }}</span></span>
            <ExternalLink v-if="external(l.url)" :size="13" class="faint" aria-hidden="true" />
          </component>
        </div>
        <p v-if="!(cfg.links || []).length" class="empty-p">No links yet.</p>
      </template>

      <template v-else-if="item.type === 'services'">
        <div class="lgrid" style="--cols: 3">
          <a v-for="s in services" :key="`${s.host}/${s.name}`" class="lk" :href="s.url" target="_blank" rel="noopener noreferrer" :title="`${s.host}: ${STATUS[s.status]?.[1] || s.status}`">
            <span class="sdot" :class="STATUS[s.status]?.[0]" :aria-label="STATUS[s.status]?.[1]"></span>
            <span class="lt"><span class="ln">{{ s.name }}</span><span class="small faint ell">{{ s.fun || s.host }}</span></span>
          </a>
        </div>
        <p v-if="svcQ && svcQ.data.value && !services.length" class="empty-p">No service with a link{{ cfg.tag || cfg.zone || cfg.category ? ' matches' : '' }}.</p>
      </template>

      <p v-else-if="item.type === 'note'" class="note">{{ cfg.text }}</p>
    </div>
  </section>
</template>

<style scoped>
.cardfill { margin: 0; height: 100%; display: flex; flex-direction: column; min-height: 0; }
.cardfill > .body { overflow: auto; flex: 1; min-height: 0; }
section.card.cardfill:not(.flush) { padding: 12px 14px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; height: 100%; }
@container (max-width: 520px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } .tile .num { font-size: calc(24px * var(--text)); } }
.tile { display: block; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; color: var(--ink); }
.tile:hover { text-decoration: none; border-color: var(--accent); }
.tile .lbl { font-size: calc(13px * var(--text)); font-weight: 500; color: var(--muted); }
.tile .num { font-size: calc(30px * var(--text)); font-weight: 650; line-height: 1.2; margin: 4px 0 2px; font-variant-numeric: tabular-nums; }
.sp { margin-left: 10px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
.dot.up { background: var(--ok); } .dot.down { background: var(--err); }
.ch { display: flex; align-items: center; gap: 10px; padding: 14px 18px 6px; }
.ch h2, .ch h3 { margin: 0; flex: 1; font-size: calc(15px * var(--text)); }
.ch.sub { padding-top: 10px; border-top: 1px solid var(--line); }
.rows { list-style: none; margin: 0; padding: 0 0 6px; }
.rows li { display: flex; align-items: center; gap: 10px; padding: 7px 18px; border-top: 1px solid var(--line); }
.rows li:first-child { border-top: 0; }
.rl { display: flex; flex-direction: column; min-width: 0; flex: 1; color: var(--ink); }
.rl:hover { text-decoration: none; } .rl:hover .t { color: var(--accent-soft); }
.empty-p { padding: 4px 18px 14px; margin: 0; color: var(--faint); font-size: calc(13px * var(--text)); }
.zones { display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 4px 18px 14px; }
.zrow { display: inline-flex; align-items: center; gap: 6px; color: var(--ink); }
.lgrid { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); gap: 8px; padding: 4px 14px 14px; }
@container (max-width: 420px) { .lgrid { grid-template-columns: minmax(0, 1fr); } }
.lk { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--r-ctl); color: var(--ink); min-width: 0; background: var(--panel); }
.lk:hover { text-decoration: none; border-color: var(--accent); }
.mono-i { width: 28px; height: 28px; border-radius: 7px; display: grid; place-items: center; font-weight: 700; flex: none;
  background: var(--accent-faint); color: var(--accent-soft); }
.lt { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.ln { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sdot { width: 9px; height: 9px; border-radius: 50%; flex: none; background: var(--line-strong); }
.sdot.up { background: var(--ok); } .sdot.down { background: var(--err); }
.note { white-space: pre-wrap; margin: 0; padding: 4px 18px 14px; }
</style>
