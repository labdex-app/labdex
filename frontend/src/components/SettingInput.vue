<script setup>
// One field of a plugin settings form, driven by the plugin.yaml schema.
import { Plus, X } from 'lucide-vue-next'
import { computed } from 'vue'

const props = defineProps({
  field: Object, modelValue: null, secretSet: Boolean, secretRef: String,
  networks: { type: Array, default: () => [] }, secrets: { type: Array, default: null },
})
const emit = defineEmits(['update:modelValue'])
const f = computed(() => props.field)
const set = (v) => emit('update:modelValue', v)
const listText = computed(() => (Array.isArray(props.modelValue) ? props.modelValue.join('\n') : props.modelValue || ''))
const toList = (t) => t.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean)
const opt = (o) => ({ value: o?.value ?? o, label: o?.label ?? o?.value ?? o })
function toggleNet(name, on) {
  const cur = new Set(props.modelValue || [])
  if (on) cur.add(name); else cur.delete(name)
  set([...cur])
}

// secrets: a value typed here, or a reference to a vault entry's field (vault:path#field; an older
// bare name means vault:global/<name>#value). The list holds what you may reveal, so may point at.
const refValue = computed(() => (props.modelValue && typeof props.modelValue === 'object' ? props.modelValue.$ref : null))
const secretMode = computed(() => (refValue.value || (props.secretRef && props.modelValue === '') ? 'ref' : 'value'))
const currentRef = computed(() => refValue.value || (props.modelValue === '' ? props.secretRef : '') || '')
function pickMode(m) { set(m === 'ref' ? { $ref: props.secrets?.[0]?.ref || '' } : '') }
const refChoices = computed(() => {
  const list = (props.secrets || []).map((s) => ({ value: s.ref, text: `${s.path}#${s.field}${s.description ? ` — ${s.description}` : ''}` }))
  const cur = currentRef.value
  return cur && !list.some((x) => x.value === cur) ? [{ value: cur, text: cur.startsWith('vault:') ? cur.slice(6) : `global/${cur}#value` }, ...list] : list
})

// tables
const rows = computed(() => (Array.isArray(props.modelValue) ? props.modelValue : []))
const blankRow = () => Object.fromEntries(f.value.columns.map((c) => [c.key, c.type === 'bool' ? false : '']))
function setCell(i, key, v) { set(rows.value.map((r, n) => (n === i ? { ...r, [key]: v } : r))) }
</script>

<template>
  <label v-if="f.type === 'bool'" class="check">
    <input type="checkbox" :checked="!!modelValue" @change="set($event.target.checked)"> {{ f.label || f.key }}
  </label>
  <template v-else>
    <span class="flabel">{{ f.label || f.key }}<b v-if="f.required" class="req" title="required">*</b></span>

    <template v-if="f.type === 'secret'">
      <div v-if="secrets?.length || secretMode === 'ref'" class="row" style="gap: 4px; margin-bottom: 4px">
        <div class="seg" role="radiogroup" :aria-label="`${f.label || f.key}: where the value comes from`">
          <button type="button" role="radio" :aria-checked="secretMode === 'value'" :class="{ on: secretMode === 'value' }" @click="pickMode('value')">Own value</button>
          <button type="button" role="radio" :aria-checked="secretMode === 'ref'" :class="{ on: secretMode === 'ref' }" :disabled="!secrets?.length && secretMode !== 'ref'" @click="pickMode('ref')">From the vault</button>
        </div>
      </div>
      <select v-if="secretMode === 'ref'" class="mono" :value="currentRef" :aria-label="`${f.label || f.key} from the vault`" @change="set({ $ref: $event.target.value })">
        <option v-for="o in refChoices" :key="o.value" :value="o.value">{{ o.text }}</option>
      </select>
      <input v-else type="password" autocomplete="new-password" :value="typeof modelValue === 'string' && modelValue !== '__clear__' ? modelValue : ''"
             :placeholder="secretSet && !secretRef ? '•••••••• (stored; type to replace)' : ''" @input="set($event.target.value)">
    </template>
    <input v-else-if="f.type === 'int'" type="number" :min="f.min" :max="f.max" :value="modelValue"
           @input="set($event.target.value === '' ? null : Number($event.target.value))">
    <select v-else-if="f.type === 'select'" :value="modelValue" @change="set($event.target.value)">
      <option v-if="!f.required" value="">—</option>
      <option v-for="o in (f.options || []).map(opt)" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>
    <textarea v-else-if="f.type === 'text'" rows="4" :value="modelValue" @input="set($event.target.value)"></textarea>
    <textarea v-else-if="f.type === 'yaml'" rows="5" spellcheck="false" :value="typeof modelValue === 'string' ? modelValue : (modelValue && Object.keys(modelValue).length ? JSON.stringify(modelValue, null, 2) : '')"
              @input="set($event.target.value)"></textarea>
    <textarea v-else-if="f.type === 'list'" rows="3" :value="listText" placeholder="one per line" spellcheck="false"
              @input="set(toList($event.target.value))"></textarea>
    <div v-else-if="f.type === 'networks'" class="nets">
      <button v-for="n in networks" :key="n.name" type="button" class="chip" :class="{ on: (modelValue || []).includes(n.name) }"
              :aria-pressed="(modelValue || []).includes(n.name)" :title="n.prefix4" @click="toggleNet(n.name, !(modelValue || []).includes(n.name))">
        <span class="mono">{{ n.name }}</span></button>
      <span v-if="!networks.length" class="empty-line small">No networks you can see.</span>
    </div>
    <div v-else-if="f.type === 'table'" class="table-wrap">
      <table class="edit">
        <thead v-if="rows.length"><tr><th v-for="c in f.columns" :key="c.key">{{ c.label || c.key }}</th><th></th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in rows" :key="i">
            <td v-for="c in f.columns" :key="c.key">
              <label v-if="c.type === 'bool'" class="check"><input type="checkbox" :checked="!!r[c.key]" :aria-label="c.label || c.key" @change="setCell(i, c.key, $event.target.checked)"></label>
              <select v-else-if="c.type === 'select'" :value="r[c.key]" @change="setCell(i, c.key, $event.target.value)">
                <option value="">—</option>
                <option v-for="o in (c.options || []).map(opt)" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
              <input v-else :type="c.type === 'int' ? 'number' : 'text'" class="mono" :value="r[c.key]"
                     @input="setCell(i, c.key, c.type === 'int' && $event.target.value !== '' ? Number($event.target.value) : $event.target.value)">
            </td>
            <td><button type="button" class="btn small ghost" aria-label="Remove row" @click="set(rows.filter((_, n) => n !== i))"><X :size="14" /></button></td>
          </tr>
        </tbody>
      </table>
      <button type="button" class="btn small mt-2" @click="set([...rows, blankRow()])"><Plus :size="14" /> Row</button>
    </div>
    <input v-else type="text" :value="modelValue" :placeholder="f.placeholder" :class="{ mono: /url|path|host|zone|endpoint|bucket|prefix|server|room|topic/.test(f.key) }" spellcheck="false"
           @input="set($event.target.value)">
  </template>
  <div v-if="f.help" class="hint">{{ f.help }}</div>
  <div v-if="f.type === 'secret' && secretSet && secretMode === 'value'" class="hint">
    Stored encrypted. <a href="#" @click.prevent="set('__clear__')">Clear it</a>
    <span v-if="modelValue === '__clear__'" class="c-red"> — will be removed on save</span>
  </div>
</template>

<style scoped>
.flabel { display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 4px; }
.req { color: var(--accent-soft); margin-left: 3px; }
.nets { display: flex; flex-wrap: wrap; gap: 6px; }
</style>
