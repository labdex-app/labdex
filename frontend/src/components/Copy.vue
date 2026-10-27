<script setup>
import { copy } from '../store'
defineProps({ text: { type: String, required: true } })
</script>

<template>
  <span class="copy mono" role="button" tabindex="0" :title="`Copy ${text}`"
        @click.stop="copy(text)" @keydown.enter.stop="copy(text)">{{ text }}</span>
</template>
