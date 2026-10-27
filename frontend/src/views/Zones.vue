<script setup>
// Areas and their zones. Everyone can look; administrators change them.
import AdminZones from '../components/AdminZones.vue'
import { state } from '../store'
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>Areas and zones</h1>
      <span class="grow"></span>
      <span v-if="!state.meta.is_admin" class="lock">🔒 only administrators change zones</span>
    </div>
    <fieldset class="bare" :disabled="!state.meta.is_admin">
      <AdminZones />
    </fieldset>
  </div>
</template>
