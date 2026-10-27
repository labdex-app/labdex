<script setup>
// Zone → segment → host, with up/down dots from discovery. Open state is remembered per browser.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { state } from '../store'

const route = useRoute()
const q = ref('')
const KEY = 'labdex-tree-open'
let saved = {}
try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') } catch { saved = {} }
const open = ref(saved)
watch(open, (v) => { try { localStorage.setItem(KEY, JSON.stringify(v)) } catch { /* private mode */ } }, { deep: true })
const toggle = (k) => { open.value = { ...open.value, [k]: !open.value[k] } }

const needle = computed(() => q.value.trim().toLowerCase())
const match = (h) => !needle.value || h.name.includes(needle.value) || (h.fun || '').includes(needle.value)
const tree = computed(() => (state.nav?.tree || []).map((z) => ({
  ...z,
  networks: z.networks.map((n) => ({ ...n, hosts: n.hosts.filter(match) })).filter((n) => !needle.value || n.hosts.length),
  hosts: z.hosts.filter(match),
})).filter((z) => !needle.value || z.networks.length || z.hosts.length))
const isOpen = (k) => !!needle.value || !!open.value[k]
const current = computed(() => (route.path.startsWith('/hosts/') ? route.params.name : null))
</script>

<template>
  <div class="tree">
    <div class="thead">
      <input v-model="q" type="search" class="tq" placeholder="Find host…" aria-label="Find host in tree">
    </div>
    <div v-for="z in tree" :key="z.zone" class="z">
      <button class="row-btn" :aria-expanded="isOpen(z.zone)" @click="toggle(z.zone)">
        <span class="car">{{ isOpen(z.zone) ? '▾' : '▸' }}</span>
        <span class="zcode" :style="{ color: z.hex }">{{ z.zone }}</span>
        <span class="grow"></span>
        <span class="cnt" :title="`${z.up} of ${z.count} seen up`">{{ z.count }}</span>
      </button>
      <template v-if="isOpen(z.zone)">
        <div v-for="n in z.networks" :key="n.name" class="n">
          <button class="row-btn" @click="toggle(`${z.zone}/${n.name}`)">
            <span class="car">{{ isOpen(`${z.zone}/${n.name}`) ? '▾' : '▸' }}</span>
            <RouterLink :to="`/networks/${n.name}`" class="nname" @click.stop>{{ n.name }}</RouterLink>
            <span v-if="n.vlan" class="faint small">{{ n.vlan }}</span>
            <span class="grow"></span><span class="cnt">{{ n.hosts.length }}</span>
          </button>
          <template v-if="isOpen(`${z.zone}/${n.name}`)">
            <RouterLink v-for="h in n.hosts" :key="h.name" :to="`/hosts/${h.name}`" class="h" :class="{ cur: current === h.name }" :title="h.name">
              <span class="dot" :class="h.live" :title="h.live"></span>{{ h.fun || h.name }}
            </RouterLink>
            <div v-if="!n.hosts.length" class="h faint">empty</div>
          </template>
        </div>
        <RouterLink v-for="h in z.hosts" :key="h.name" :to="`/hosts/${h.name}`" class="h lone" :class="{ cur: current === h.name }">
          <span class="dot" :class="h.live"></span>{{ h.fun || h.name }}
        </RouterLink>
      </template>
    </div>
    <p v-if="state.nav && !state.nav.zones_defined" class="small faint" style="padding: 4px 10px">No zones yet.
      <RouterLink v-if="state.meta?.is_admin" to="/zones">Create them</RouterLink></p>
  </div>
</template>

<style scoped>
.sect { font-size: 12px; color: var(--faint); margin: 0 8px 4px 0; padding-left: 8px; font-weight: 500; }
.thead { position: sticky; top: 30px; z-index: 2; background: var(--panel); padding: 2px 0; }
.tq { margin: 2px 0 8px; padding: 5px 9px !important; font-size: 12.5px !important; }
.row-btn { width: 100%; display: flex; align-items: center; gap: 6px; font: inherit; background: none; border: 0; color: var(--muted);
  padding: 3px 8px; border-radius: 6px; cursor: pointer; text-align: left; }
.row-btn:hover { background: var(--raise); color: var(--ink); }
.car { width: 10px; color: var(--faint); font-size: 10px; }
.zcode { font-family: var(--mono); font-size: 12px; font-weight: 700; }
.cnt { font-size: 11px; color: var(--faint); }
.n .row-btn { padding-left: 20px; }
.nname { color: var(--muted); font-size: 13px; }
.h { display: flex; align-items: center; gap: 7px; padding: 2px 8px 2px 40px; font-size: 13px; color: var(--muted); border-radius: 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.h.lone { padding-left: 26px; }
.h:hover { background: var(--raise); color: var(--ink); text-decoration: none; }
.h.cur { background: var(--accent-faint); color: var(--ink); }
.dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--faint); opacity: .5; }
.dot.up { background: var(--ok); opacity: 1; box-shadow: 0 0 6px rgba(74, 222, 128, .6); }
.dot.down { background: var(--err); opacity: 1; }
</style>
