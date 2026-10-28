<script setup>
// Jobs that ran for one host: its run history.
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import JobBadge from './JobBadge.vue'
import { useQuery } from '../query'
import { ago } from '../store'
import Loading from './Loading.vue'

const props = defineProps({ host: { type: String, required: true } })
const router = useRouter()
const q = useQuery(() => `/api/jobs?host=${encodeURIComponent(props.host)}&limit=50`, { staleMs: 5000 })
const runs = computed(() => q.data.value || [])
</script>

<template>
  <div class="card flush">
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Integration</th><th>Action</th><th>Status</th><th>Trigger</th><th>When</th></tr></thead>
        <tbody>
          <tr v-for="j in runs" :key="j.id" class="click" v-rowlink @click="router.push(`/jobs/${j.id}`)">
            <td class="faint">{{ j.id }}</td>
            <td>{{ j.integration }}</td>
            <td>{{ j.action }}<span v-if="j.hosts.length > 1" class="faint small"> · {{ j.hosts.length }} hosts</span></td>
            <td><JobBadge :status="j.status" /></td>
            <td class="small">{{ j.trigger }}<span v-if="j.trigger === 'manual'" class="faint"> · {{ j.created_by }}</span></td>
            <td class="small">{{ ago(j.created_at) }}</td>
          </tr>
          <tr v-if="q.loading.value && !runs.length"><td colspan="6"><Loading /></td></tr>
          <tr v-else-if="!runs.length"><td colspan="6" class="empty">Nothing has run for this host yet. Host actions and Ansible runs that include it show up here.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
