<script setup>
import { computed } from 'vue'
import { ICONS, iconFor } from '../icons'
const props = defineProps({ host: { type: Object, default: null }, icon: { type: String, default: '' }, size: { type: Number, default: 16 } })
const comp = computed(() => ICONS[props.icon || iconFor(props.host)] || ICONS.server)
</script>

<template>
  <component :is="comp" :size="size" :stroke-width="1.75" aria-hidden="true" class="hicon" />
</template>

<style scoped>
.hicon { flex: none; vertical-align: -3px; }
</style>
