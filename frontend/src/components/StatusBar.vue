<script setup>
// The status bar at the foot of every page: is the live stream up, which commit each site is
// synced to and when, how many agents report, whether the vault is sealed. Docs, source and
// version on the right.
import { computed } from 'vue'
import { ago, state } from '../store'

const sites = computed(() => (state.bar?.sites || []).filter((s) => s.git))
const agents = computed(() => state.bar?.agents)
const vault = computed(() => state.bar?.vault)
</script>

<template>
  <footer class="sbar" aria-label="Status">
    <span class="it" :class="state.live ? 'ok' : 'off'" :title="state.live ? 'Live: changes show as they happen' : 'Not connected: reconnecting'">
      <span class="dot" :class="state.live ? 'ok' : 'unknown'" aria-hidden="true"></span>{{ state.live ? 'live' : 'offline' }}</span>
    <RouterLink v-for="s in sites" :key="s.code" :to="state.meta?.is_admin ? '/admin/sites' : '/history'" class="it hide-p"
                :title="s.git.error ? `${s.code}: the last git sync failed` : `${s.code}: synced to ${s.git.commit || 'nothing yet'}`">
      {{ s.code }} <b :class="{ bad: s.git.error }">{{ s.git.commit || '—' }}</b> {{ s.git.error ? 'sync failed' : `synced ${ago(s.git.synced_at)}` }}</RouterLink>
    <span v-if="agents?.total" class="it hide-p" title="Agents linked now, of all enrolled">agents <b>{{ agents.linked }}/{{ agents.total }}</b></span>
    <RouterLink v-if="vault?.initialized" to="/vault" class="it hide-p" :class="{ warn: vault.sealed }">vault <b>{{ vault.sealed ? 'sealed' : 'unsealed' }}</b></RouterLink>
    <span class="grow"></span>
    <RouterLink to="/help" class="it">Docs</RouterLink>
    <a v-if="state.meta?.project_url" :href="state.meta.project_url" target="_blank" rel="noopener" class="it hide-p">Source</a>
    <RouterLink to="/about" class="it">labdex {{ state.meta?.version }}</RouterLink>
  </footer>
</template>

<style scoped>
.sbar { position: sticky; bottom: 0; z-index: 11; display: flex; align-items: center; gap: 18px; height: 28px; padding: 0 16px 0 22px;
  border-top: 1px solid var(--line); background: var(--panel); font: calc(11.5px * var(--text)) var(--mono); color: var(--faint); white-space: nowrap; overflow: hidden; }
.it { display: inline-flex; align-items: center; gap: 6px; color: var(--faint); }
a.it:hover { color: var(--ink); text-decoration: none; }
.it b { font-weight: 500; color: var(--muted); }
.it b.bad, .it.warn b { color: var(--err); }
.it.ok { color: var(--ok); }
.grow { flex: 1; }
@media print { .sbar { display: none; } }
</style>
