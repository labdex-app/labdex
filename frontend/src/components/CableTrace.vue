<script setup>
// A cable's whole path, end to end through patch panels: one row of stops, each joined to the
// next by a cable (its label, colour and length) or by the panel itself (front to rear).
import { ArrowLeftRight, Cable } from 'lucide-vue-next'
import { ref, watch } from 'vue'
import { api } from '../api'
import { cableHex } from '../links'

const props = defineProps({ host: { type: String, required: true }, port: { type: String, required: true } })
const t = ref(null)
const error = ref('')
async function load() {
  t.value = null
  error.value = ''
  try { t.value = await api.get(`/api/cables/trace?host=${encodeURIComponent(props.host)}&port=${encodeURIComponent(props.port)}`) } catch (e) { error.value = e.message }
}
watch(() => [props.host, props.port], load, { immediate: true })
const isStart = (p) => p.host === props.host && p.port.toLowerCase() === props.port.toLowerCase()
</script>

<template>
  <div class="trace">
    <p v-if="error" class="err">{{ error }}</p>
    <p v-else-if="!t" class="small faint">Tracing…</p>
    <template v-else>
      <p class="small muted sum">
        <template v-if="!t.cables">No cable on this port{{ t.points.length > 1 ? ' or on the port it passes through to' : '' }}.</template>
        <template v-else>{{ t.cables }} cable{{ t.cables === 1 ? '' : 's' }}{{ t.points.length - 1 > t.cables ? `, through ${t.points.length - 1 - t.cables} patch panel port pair${t.points.length - 1 - t.cables === 1 ? '' : 's'}` : '' }}.
          <span v-if="t.loop" class="c-red">The path runs in a circle: check the pass-through ports.</span></template>
      </p>
      <ol class="path">
        <template v-for="(p, n) in t.points" :key="n">
          <li class="stop" :class="{ start: isStart(p), end: n === 0 || n === t.points.length - 1 }">
            <template v-if="p.hidden"><span class="faint">a host you can't see</span></template>
            <template v-else>
              <RouterLink :to="`/hosts/${p.uid || p.host}?tab=network`" class="h">{{ p.host }}</RouterLink>
              <span class="mono pt">{{ p.port }}</span>
            </template>
          </li>
          <li v-if="n < t.links.length" class="link" :class="{ through: t.links[n].through }" aria-hidden="false">
            <template v-if="t.links[n].through"><ArrowLeftRight :size="14" /><span class="small faint">through the panel</span></template>
            <template v-else>
              <i class="wire" :style="{ background: cableHex(t.links[n].cable.colour) || 'var(--line-strong)' }"></i>
              <span class="small"><Cable :size="13" /> {{ [t.links[n].cable.label, t.links[n].cable.medium, t.links[n].cable.speed, t.links[n].cable.length ? `${t.links[n].cable.length} m` : '', t.links[n].cable.colour].filter(Boolean).join(' · ') || 'cable' }}</span>
            </template>
          </li>
        </template>
      </ol>
    </template>
  </div>
</template>

<style scoped>
.sum { margin: 0 0 12px; }
.path { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.stop { display: flex; align-items: baseline; gap: 8px; padding: 8px 12px; border: 1px solid var(--line-strong); border-radius: var(--r-ctl); background: var(--card); }
.stop.start { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.stop .h { font-weight: 600; }
.stop .pt { margin-left: auto; }
.link { display: flex; align-items: center; gap: 10px; padding: 6px 0 6px 22px; color: var(--muted); min-height: 30px; }
.link .wire { width: 4px; align-self: stretch; border-radius: 2px; min-height: 22px; }
.link.through { padding-left: 16px; }
.link.through :deep(svg) { color: var(--faint); }
</style>
