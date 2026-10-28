<script setup>
// A list of values as chips. Type to filter the suggestions; Enter, comma or a click adds one.
// New values are allowed unless `strict`; `normalize` cleans what was typed.
import { computed, ref } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  suggestions: { type: Array, default: () => [] }, // strings or {value, label, group}
  placeholder: { type: String, default: '' },
  strict: { type: Boolean, default: false },
  normalize: { type: Function, default: (v) => v.trim() },
  mono: { type: Boolean, default: false },
  label: { type: String, default: 'Add' },
  max: { type: Number, default: 12 },
  warn: { type: Function, default: null }, // value -> '' or why it's suspicious (the chip is marked)
  colour: { type: Function, default: null }, // value -> a colour for its chip, or ''
})
const emit = defineEmits(['update:modelValue'])
const text = ref('')
const focus = ref(false)
const active = ref(0)
const items = computed(() => props.suggestions.map((s) => (typeof s === 'string' ? { value: s, label: '', group: '' } : s)))
const known = computed(() => new Set(items.value.map((s) => s.value)))
const matches = computed(() => {
  const n = text.value.trim().toLowerCase()
  return items.value.filter((s) => !props.modelValue.includes(s.value) && (!n || `${s.value} ${s.label}`.toLowerCase().includes(n))).slice(0, props.max)
})
const isNew = computed(() => {
  const v = props.normalize(text.value)
  return v && !props.strict && !known.value.has(v) && !props.modelValue.includes(v)
})

function add(v) {
  v = props.normalize(v ?? text.value)
  if (!v || props.modelValue.includes(v) || (props.strict && !known.value.has(v))) return
  emit('update:modelValue', [...props.modelValue, v])
  text.value = ''
  active.value = 0
}
function blur() {
  window.setTimeout(() => {
    focus.value = false
    if (text.value.trim() && !props.strict) add() // a value typed but not confirmed still counts
  }, 150)
}
function remove(i) { const next = [...props.modelValue]; next.splice(i, 1); emit('update:modelValue', next) }
function key(e) {
  if (e.key === 'Enter' || e.key === ',' || e.key === 'Tab') {
    if (!text.value.trim()) return
    e.preventDefault()
    add(matches.value[active.value] && !isNew.value ? matches.value[active.value].value : text.value)
  } else if (e.key === 'ArrowDown') { e.preventDefault(); active.value = Math.min(active.value + 1, matches.value.length - 1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); active.value = Math.max(active.value - 1, 0) }
  else if (e.key === 'Backspace' && !text.value && props.modelValue.length) remove(props.modelValue.length - 1)
}
function paste(e) {
  const t = e.clipboardData?.getData('text') || ''
  if (/[\s,]/.test(t.trim())) {
    e.preventDefault()
    const next = [...props.modelValue]
    for (const p of t.split(/[\s,]+/).map(props.normalize).filter(Boolean)) if (!next.includes(p) && (!props.strict || known.value.has(p))) next.push(p)
    emit('update:modelValue', next)
  }
}
</script>

<template>
  <div class="chipin" :class="{ focus }">
    <span v-for="(v, i) in modelValue" :key="v" class="ch" :class="{ mono, warnch: warn && warn(v), col: colour && colour(v) }" :title="(warn && warn(v)) || ''"
          :style="colour && colour(v) ? { '--tc': colour(v) } : {}">{{ v }}
      <button type="button" :aria-label="`Remove ${v}`" @click="remove(i)">✕</button></span>
    <input v-model="text" :class="{ mono }" :placeholder="modelValue.length ? '' : placeholder" :aria-label="label" autocomplete="off" spellcheck="false"
           @keydown="key" @paste="paste" @focus="focus = true" @blur="blur" @input="active = 0">
    <ul v-if="focus && (matches.length || isNew)" class="dd pop" role="listbox">
      <li v-if="isNew" class="pop-item" :class="{ on: !matches.length }" @mousedown.prevent="add()">+ new <b :class="{ mono }">{{ normalize(text) }}</b></li>
      <li v-for="(s, i) in matches" :key="s.value" class="pop-item" :class="{ on: i === active && !isNew }" role="option" @mousedown.prevent="add(s.value)">
        <span :class="{ mono }">{{ s.value }}</span><span v-if="s.label" class="faint small"> {{ s.label }}</span><span v-if="s.group" class="grp">{{ s.group }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.chipin { position: relative; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; min-height: 36px; padding: 3px 6px;
  border: 1px solid var(--line-strong); border-radius: var(--r-ctl); background: var(--panel); }
.chipin.focus { border-color: var(--accent); }
.chipin input { flex: 1; min-width: 90px; border: 0 !important; background: none !important; padding: 4px !important; outline: none; box-shadow: none !important; }
.ch { display: inline-flex; align-items: center; gap: 4px; padding: 1px 4px 1px 9px; border-radius: 999px; background: var(--raise); font-size: 12.5px; }
.ch.col { color: var(--tc); background: color-mix(in srgb, var(--tc) 14%, transparent); }
.ch.warnch { background: rgba(251, 191, 36, .14); color: var(--c-yellow); }
.ch button { border: 0; background: none; color: var(--faint); cursor: pointer; font-size: 11px; padding: 0 3px; }
.ch button:hover { color: var(--ink); }
.dd { position: absolute; left: 0; right: 0; top: 100%; margin-top: 4px; z-index: 20; max-height: 260px; overflow: auto; }
.dd li { gap: 6px; align-items: baseline; }
.grp { margin-left: auto; font-size: 11px; color: var(--faint); }
</style>
