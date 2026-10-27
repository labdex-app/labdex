<script setup>
// Hostname parts and fun name pools. Everyone can look; administrators change them.
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminNaming from '../components/AdminNaming.vue'
import FunPools from '../components/FunPools.vue'
import TagList from '../components/TagList.vue'
import { state } from '../store'

const route = useRoute()
const router = useRouter()
const tab = computed(() => (['pools', 'tags'].includes(route.query.tab) ? route.query.tab : 'parts'))
const setTab = (t) => router.replace({ query: { ...route.query, tab: t } })
const q = ref('')
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Naming</h1>
      <span class="sub">hostnames, fun names and tags</span>
      <span class="grow"></span>
      <input v-model="q" type="search" :placeholder="tab === 'pools' ? 'Search pools and names' : tab === 'tags' ? 'Search tags' : 'Search codes and labels'" style="max-width: 260px" aria-label="Search">
      <span v-if="!state.meta.is_admin" class="lock">🔒 only administrators change these lists</span>
    </div>
    <nav class="tabs">
      <a href="#" :class="{ 'router-link-active': tab === 'parts' }" @click.prevent="setTab('parts')">Hostname parts</a>
      <a href="#" :class="{ 'router-link-active': tab === 'pools' }" @click.prevent="setTab('pools')">Fun name pools</a>
      <a href="#" :class="{ 'router-link-active': tab === 'tags' }" @click.prevent="setTab('tags')">Tags</a>
    </nav>
    <fieldset class="bare" :disabled="!state.meta.is_admin">
      <AdminNaming v-if="tab === 'parts'" :q="q" />
      <FunPools v-else-if="tab === 'pools'" :q="q" />
    </fieldset>
    <TagList v-if="tab === 'tags'" :q="q" />
  </div>
</template>
