<script setup>
defineProps({ status: String })
</script>
<template><span class="js" :class="`js-${status}`">{{ status }}</span></template>
