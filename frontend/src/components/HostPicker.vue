<script setup>
// Pick an existing host by typing part of its name or fun name.
// The list is rendered on <body> with fixed positioning, so tables and cards that
// clip their overflow (discovery rows) can't cut it off.
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useQuery } from '../query'

const props = defineProps({ placeholder: { type: String, default: 'Host…' }, zone: String })
const emit = defineEmits(['pick'])
// Only the fields it needs: a fraction of the full host list, shared by every picker on the page.
const all = useQuery('/api/hosts?fields=name,fun,zone,kind,can_edit')
const hosts = computed(() => (all.data.value || []).filter((h) => h.can_edit))
const q = ref('')
const open = ref(false)
const active = ref(0)
const input = ref(null)
const pos = ref({ top: 0, left: 0, width: 0, up: false })

const hits = computed(() => {
  const n = q.value.trim().toLowerCase()
  const rows = hosts.value.filter((h) => !n || h.name.includes(n) || (h.fun || '').toLowerCase().includes(n))
  return rows.sort((a, b) => (b.zone === props.zone) - (a.zone === props.zone) || a.name.localeCompare(b.name)).slice(0, 12)
})

function place() {
  const r = input.value?.getBoundingClientRect()
  if (!r) return
  const below = window.innerHeight - r.bottom
  pos.value = { left: Math.min(r.left, window.innerWidth - 380), width: Math.max(r.width, 340), up: below < 300 && r.top > below,
                top: r.bottom + 2, bottom: window.innerHeight - r.top + 2 }
}
function show() { open.value = true; active.value = 0; nextTick(place) }
function close() { setTimeout(() => { open.value = false }, 150) }
function pick(h) { emit('pick', h.name); q.value = ''; open.value = false }
function key(e) {
  if (!open.value) show()
  if (e.key === 'ArrowDown') { active.value = Math.min(active.value + 1, hits.value.length - 1); e.preventDefault() }
  if (e.key === 'ArrowUp') { active.value = Math.max(active.value - 1, 0); e.preventDefault() }
  if (e.key === 'Enter') { e.preventDefault(); if (hits.value[active.value]) pick(hits.value[active.value]) }
  if (e.key === 'Escape') open.value = false
}
const onScroll = () => { if (open.value) place() }
window.addEventListener('scroll', onScroll, true)
window.addEventListener('resize', onScroll)
onBeforeUnmount(() => { window.removeEventListener('scroll', onScroll, true); window.removeEventListener('resize', onScroll) })
</script>

<template>
  <div class="hp">
    <input ref="input" v-model="q" type="search" :placeholder="placeholder" class="small-in" autocomplete="off"
           role="combobox" :aria-expanded="open" @focus="show" @input="show" @blur="close" @keydown="key">
    <Teleport to="body">
      <ul v-if="open && hits.length" class="hp-drop" role="listbox"
          :style="{ left: pos.left + 'px', width: pos.width + 'px', ...(pos.up ? { bottom: pos.bottom + 'px' } : { top: pos.top + 'px' }) }">
        <li v-for="(h, n) in hits" :key="h.name" role="option" :aria-selected="n === active" :class="{ on: n === active }"
            :title="h.name" @mousedown.prevent="pick(h)" @mouseenter="active = n">
          <span class="nm mono">{{ h.name }}</span>
          <span v-if="h.fun" class="fun-n">{{ h.fun }}</span>
          <span v-if="h.kind === 'vip'" class="vip">VIP</span>
          <span class="zn" :class="{ same: h.zone === zone }">{{ h.zone }}</span>
        </li>
      </ul>
    </Teleport>
  </div>
</template>

<style scoped>
.hp { position: relative; min-width: 190px; }
.small-in { padding: 4px 8px !important; font-size: calc(12.5px * var(--text)) !important; width: 100%; }
</style>

<style>
/* not scoped: the list lives on <body> */
.hp-drop { position: fixed; z-index: 1000; margin: 0; padding: 4px; list-style: none; max-height: 300px; overflow-y: auto;
  background: var(--card); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 12px 28px var(--shadow); }
.hp-drop li { display: flex; align-items: baseline; gap: 8px; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-size: calc(12.5px * var(--text)); }
.hp-drop li.on { background: var(--raise); }
.hp-drop .nm { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.hp-drop .fun-n { color: var(--faint); white-space: nowrap; }
.hp-drop .zn { color: var(--faint); font-size: calc(11px * var(--text)); white-space: nowrap; }
.hp-drop .zn.same { color: var(--ink); }
.hp-drop .vip { font-size: calc(10px * var(--text)); padding: 0 5px; border: 1px solid var(--line); border-radius: 4px; color: var(--faint); }
</style>
