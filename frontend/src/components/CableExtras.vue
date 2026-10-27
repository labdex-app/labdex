<script setup>
// What's on a cable besides its ends: the printed label (with the next free one a click away),
// its length in metres and the jacket colour. Edits the object it's given.
import { ref } from 'vue'
import { api } from '../api'
import { CABLE_COLOURS } from '../links'
import Field from './Field.vue'

const cable = defineModel('cable', { type: Object, required: true })
defineProps({ errors: { type: Object, default: () => ({}) }, compact: Boolean })
const next = ref('')
async function nextFree() {
  try { next.value = (await api.get('/api/cables')).next_label } catch { next.value = '' }
  if (next.value) cable.value.label = next.value
}
</script>

<template>
  <div :class="compact ? 'g2' : 'g3'">
    <Field label="Label" :error="errors.label" :hint="compact ? '' : 'What’s printed on it'">
      <div class="inline"><input v-model.trim="cable.label" class="mono" placeholder="C-0001" aria-label="Cable label">
        <button type="button" class="btn small" title="The next free label" @click="nextFree">Next</button></div></Field>
    <Field label="Length" :error="errors.length" :hint="compact ? '' : 'Metres'"><input v-model.trim="cable.length" inputmode="decimal" placeholder="3" aria-label="Cable length in metres"></Field>
    <Field label="Colour" :error="errors.colour"><input v-model.trim="cable.colour" list="cable-colour-names" placeholder="blue" aria-label="Cable colour"></Field>
    <datalist id="cable-colour-names"><option v-for="x in CABLE_COLOURS" :key="x.name" :value="x.name" /></datalist>
  </div>
</template>

<style scoped>
.inline { display: flex; gap: 6px; }
.inline input { min-width: 0; }
</style>
