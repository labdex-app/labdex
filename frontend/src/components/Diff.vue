<script setup>
// Field-level diff from /api/history/<id>: [{path, before, after}]
defineProps({ rows: { type: Array, default: () => [] } })
const show = (v) => (v === null || v === undefined || v === '' ? '∅' : typeof v === 'object' ? JSON.stringify(v) : String(v))
</script>

<template>
  <div v-if="rows.length" class="table-wrap">
    <table class="diff">
      <thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.path">
          <td class="muted">{{ r.path }}</td>
          <td class="del">{{ show(r.before) }}</td>
          <td class="add">{{ show(r.after) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <p v-else class="faint" style="margin: 0">No field changes recorded.</p>
</template>
