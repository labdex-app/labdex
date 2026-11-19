<script setup>
// A job on its own page (links from history, notifications and old bookmarks).
import { useRouter } from 'vue-router'
import JobDetail from '../components/JobDetail.vue'

defineProps({ id: String })
const router = useRouter()
</script>

<template>
  <JobDetail :id="id" :key="id" @go="(n) => router.push(`/jobs/${n}`)" />
</template>
