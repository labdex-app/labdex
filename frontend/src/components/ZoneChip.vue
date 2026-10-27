<script setup>
import { zoneHex, zoneLong } from '../store'
defineProps({ zone: { type: String, required: true } })
</script>

<template>
  <span class="zone" :style="{ '--zc': zoneHex(zone) }" :title="zoneLong(zone)">{{ zone }}</span>
</template>
