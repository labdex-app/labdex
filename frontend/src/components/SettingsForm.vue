<script setup>
// A whole settings form from a schema: integration settings, host settings, action parameters.
import SettingInput from './SettingInput.vue'

// v-model:values: the settings, edited in place (one key per field)
const values = defineModel('values', { type: Object, required: true })
const props = defineProps({
  fields: { type: Array, default: () => [] }, errors: { type: Object, default: () => ({}) },
  secretsSet: { type: Array, default: () => [] }, secretRefs: { type: Object, default: () => ({}) },
  networks: { type: Array, default: () => [] }, secrets: { type: Array, default: null }, disabled: Boolean,
})
// `when: {auth: [password]}` in plugin.yaml: the field only shows while those settings match
const val = (k) => values.value?.[k] ?? props.fields.find((x) => x.key === k)?.default
const shown = (f) => Object.entries(f.when || {}).every(([k, w]) => (Array.isArray(w) ? w : [w]).includes(val(k)))
</script>

<template>
  <fieldset class="bare sform" :disabled="disabled">
    <div v-for="f in fields.filter(shown)" :key="f.key">
      <SettingInput v-model="values[f.key]" :field="f" :secret-set="secretsSet.includes(f.key)" :secret-ref="secretRefs[f.key]"
                    :networks="networks" :secrets="secrets" />
      <div v-if="errors[f.key]" class="err">{{ errors[f.key] }}</div>
    </div>
  </fieldset>
</template>

<style scoped>
.sform > * + * { margin-top: 12px; }
</style>
