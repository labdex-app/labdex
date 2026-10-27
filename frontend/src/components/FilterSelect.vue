<script setup>
// A compact filter: "Zone: any ▾". Empty value = no filter.
defineProps({
  label: { type: String, required: true },
  modelValue: { type: [String, null], default: '' },
  options: { type: Array, required: true }, // strings or {value, label}
  any: { type: String, default: 'any' }, // label of the no-filter option; '' = no such option
})
const emit = defineEmits(['update:modelValue'])
const opt = (o) => (typeof o === 'string' ? { value: o, label: o } : o)
</script>

<template>
  <label class="fsel" :class="{ on: modelValue && any }">
    <span class="fl">{{ label }}</span>
    <select :value="modelValue || ''" :aria-label="label" @change="emit('update:modelValue', $event.target.value || null)">
      <option v-if="any" value="">{{ any }}</option>
      <option v-for="o in options.map(opt)" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>
  </label>
</template>

<style scoped>
.fsel { display: inline-flex; align-items: center; gap: 0; border: 1px solid var(--line-strong); border-radius: var(--r-ctl);
  background: var(--panel); font-size: 13px; height: 32px; overflow: hidden; }
.fsel.on { border-color: var(--accent); background: var(--accent-faint); }
.fl { color: var(--faint); padding: 0 2px 0 10px; }
.fsel select { border: 0; background: transparent; width: auto; padding: 0 26px 0 6px; height: 30px; line-height: 30px; color: var(--ink);
  cursor: pointer; font-weight: 500; }
.fsel select:focus { box-shadow: none; }
</style>
