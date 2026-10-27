<script setup>
// Recent changes to one object, for the side of an edit page.
import { onMounted, ref } from 'vue'
import { api, enc } from '../api'
import { ago } from '../store'

const props = defineProps({ type: String, name: String, limit: { type: Number, default: 8 } })
const events = ref(null)

onMounted(async () => {
  try {
    events.value = await api.get(`/api/history?object_type=${props.type}&object_name=${enc(props.name)}&limit=${props.limit}`)
  } catch {
    events.value = null // no audit:view here; hide the card
  }
})
</script>

<template>
  <div v-if="events" class="card">
    <h3>History</h3>
    <ul v-if="events.length" class="plain">
      <li v-for="e in events" :key="e.id" class="hl">
        <RouterLink :to="`/history/${e.id}`" class="op" :class="`op-${e.action}`">{{ e.action }}</RouterLink>
        <span class="small">{{ e.summary }}</span>
        <span class="small faint nowrap">{{ e.actor }} · {{ ago(e.ts) }}</span>
      </li>
    </ul>
    <p v-else class="faint" style="margin: 0">No recorded changes.</p>
    <RouterLink :to="`/history?object_type=${type}&object_name=${enc(name)}`" class="small">All changes →</RouterLink>
  </div>
</template>

<style scoped>
.hl { display: grid; grid-template-columns: 58px 1fr; gap: 0 8px; padding: 5px 0; word-break: normal; }
.hl > .nowrap { grid-column: 2; }
</style>
