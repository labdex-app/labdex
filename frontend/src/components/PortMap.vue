<script setup>
// A host's physical ports as a front panel: in their natural order, odd ones on top and even
// ones below (the way switches number them), RJ45 and SFP-style cages in blocks of their own.
// A cabled port takes the colour of the cable's speed; point at it (or focus it) for where it goes.
import { computed, ref } from 'vue'
import { isPhysical, speedStep } from '../links'

const props = defineProps({ interfaces: { type: Array, required: true }, cables: { type: Array, default: () => [] } })
const emit = defineEmits(['pick'])
const tip = ref(null)

// eth2 < eth10, 1/0/9 < 1/0/10: compare the digit runs as numbers
const natural = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
const cage = (i) => (['sfp', 'sfp+', 'sfp28', 'qsfp+', 'qsfp28', 'qsfp-dd'].includes(i.connector) ? 'cage' : 'rj45')
const blocks = computed(() => {
  const ports = props.interfaces.filter((i) => isPhysical(i) && i.label && i.kind !== 'wifi').sort((a, b) => natural(a.label, b.label))
  const out = []
  for (const kind of ['rj45', 'cage']) {
    const list = ports.filter((p) => cage(p) === kind).map((i) => {
      const c = props.cables.find((x) => x.port === i.label && x.medium !== 'virtual')
      const num = /(\d+)\D*$/.exec(i.label)?.[1] || ''
      return { i, c, num, colour: c ? speedStep(c.speed || i.speed)?.colour || 'var(--muted)' : '' }
    })
    if (!list.length) continue
    const two = list.length > 12
    out.push({ kind, two, top: two ? list.filter((_, n) => n % 2 === 0) : list, bottom: two ? list.filter((_, n) => n % 2 === 1) : [] })
  }
  return out
})
const count = computed(() => blocks.value.reduce((n, b) => n + b.top.length + b.bottom.length, 0))
const used = computed(() => blocks.value.reduce((n, b) => n + [...b.top, ...b.bottom].filter((p) => p.c).length, 0))
const describe = (p) => [p.i.label, p.c ? `→ ${p.c.peer} ${p.c.peer_port}` : 'free', p.c?.speed || p.i.speed, p.c?.label].filter(Boolean).join('  ')
function show(p, ev) {
  const r = ev.currentTarget.getBoundingClientRect(), box = ev.currentTarget.closest('.pm').getBoundingClientRect()
  tip.value = { text: describe(p), x: Math.max(0, r.left - box.left - 4), y: r.bottom - box.top + 6 }
}
</script>

<template>
  <div v-if="count >= 4" class="pm">
    <div class="pm-head small muted">Front panel · {{ used }} of {{ count }} ports cabled</div>
    <div class="panel">
      <div v-for="b in blocks" :key="b.kind" class="block" :class="b.kind">
        <div v-for="(row, r) in b.two ? [b.top, b.bottom] : [b.top]" :key="r" class="prow">
          <button v-for="p in row" :key="p.i.label" type="button" class="port" :class="{ on: p.c }" :style="p.c ? { '--pc': p.colour } : {}"
                  :aria-label="describe(p)" @mouseenter="show(p, $event)" @focus="show(p, $event)" @mouseleave="tip = null" @blur="tip = null" @click="emit('pick', p.i, p.c)">
            <span class="n">{{ p.num }}</span></button>
        </div>
      </div>
    </div>
    <div v-if="tip" class="tip mono small" role="tooltip" :style="{ left: `${tip.x}px`, top: `${tip.y}px` }">{{ tip.text }}</div>
  </div>
</template>

<style scoped>
.pm { position: relative; margin-bottom: 14px; }
.pm-head { margin-bottom: 6px; }
.panel { display: flex; gap: 14px; flex-wrap: wrap; align-items: flex-start; background: var(--panel); border: 1px solid var(--line-strong);
  border-radius: var(--r-box); padding: 10px 12px; overflow-x: auto; }
.block { display: flex; flex-direction: column; gap: 4px; }
.prow { display: flex; gap: 4px; }
.port { width: 26px; height: 22px; border-radius: var(--r-sm); border: 1.5px solid var(--line-strong); background: var(--bg); cursor: pointer; padding: 0;
  display: grid; place-items: center; font: inherit; }
.cage .port { width: 30px; height: 16px; border-radius: 2px; }
.port .n { font-size: calc(9px * var(--text)); color: var(--faint); font-family: var(--mono, 'Red Hat Mono', monospace); }
.port.on { border-color: var(--pc); background: color-mix(in srgb, var(--pc) 28%, var(--bg)); }
.port.on .n { color: var(--ink); }
.port:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.tip { position: absolute; background: var(--card); border: 1px solid var(--line-strong); border-radius: var(--r-ctl);
  padding: 4px 8px; white-space: nowrap; box-shadow: 0 8px 20px var(--shadow); pointer-events: none; z-index: 3; }
</style>
