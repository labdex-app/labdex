<script setup>
// Label + slot + validation message, so every form field looks the same.
defineProps({ label: String, error: String, hint: String })
</script>

<template>
  <label class="field">
    <span>{{ label }}</span>
    <slot />
    <div v-if="error" class="err">{{ error }}</div>
    <div v-else-if="hint" class="hint">{{ hint }}</div>
  </label>
</template>
