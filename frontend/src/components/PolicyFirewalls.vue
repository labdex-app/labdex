<script setup>
// The Policy page's "On the firewalls" tab: every integration that enforces the zone matrix
// reports how its firewall matches it (a plugin page marked `place: policy`), with the action
// that brings it in line (a plan you review) next to it.
import { RefreshCw, ShieldCheck } from 'lucide-vue-next'
import { onMounted, reactive } from 'vue'
import { api } from '../api'
import ActionButton from './ActionButton.vue'
import ModuleView from './ModuleView.vue'

const props = defineProps({ firewalls: { type: Array, default: () => [] } })
const views = reactive({}) // integration name: the page's answer (or {error})
const busy = reactive({})
const enc = encodeURIComponent

async function load(f) {
  busy[f.integration] = true
  try { views[f.integration] = await api.get(`/api/integrations/${enc(f.integration)}/pages/${enc(f.page)}`) } catch (e) { views[f.integration] = { error: e.message } }
  busy[f.integration] = false
}
onMounted(() => props.firewalls.forEach(load))
</script>

<template>
  <div class="fws">
    <div v-if="!firewalls.length" class="emptyp">
      <ShieldCheck :size="22" />
      <p>No firewall enforces the matrix yet. The OPNsense plugin does: it writes a pass rule for each allowed pair (as a plan you review)
        and lists the rules the matrix doesn't explain.</p>
      <RouterLink to="/plugins?tab=market" class="btn small">Find it in the Marketplace</RouterLink>
    </div>
    <section v-for="f in firewalls" :key="f.integration" class="card fw">
      <header>
        <h2><RouterLink :to="`/integrations/${f.integration}`">{{ f.integration }}</RouterLink></h2>
        <span class="small faint">{{ f.plugin_name }}</span>
        <span class="grow"></span>
        <button class="btn icon" :disabled="busy[f.integration]" :aria-label="`Read ${f.integration} again`" title="Read the firewall again" @click="load(f)">
          <RefreshCw :size="15" :class="{ spin: busy[f.integration] }" /></button>
      </header>
      <div v-if="f.action && f.can_apply" class="act"><ActionButton :action="f.action" primary :url="`/api/integrations/${enc(f.integration)}/actions/${f.action.id}`" /></div>
      <p v-if="!views[f.integration]" class="small faint">Reading the firewall…</p>
      <ModuleView v-else :view="views[f.integration]" />
    </section>
  </div>
</template>

<style scoped>
.fws { display: flex; flex-direction: column; gap: 16px; }
.fw { margin: 0; }
.fw header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.fw h2 { margin: 0; }
.act { padding-bottom: 12px; margin-bottom: 12px; border-bottom: 1px solid var(--line); }
.emptyp { display: flex; flex-direction: column; align-items: center; gap: 8px; color: var(--muted); padding: 32px 16px; text-align: center; }
.emptyp p { margin: 0; max-width: 60ch; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
</style>
