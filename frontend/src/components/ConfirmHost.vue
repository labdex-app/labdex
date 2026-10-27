<script setup>
import { answer, confirmState } from '../confirm'
import Modal from './Modal.vue'
</script>

<template>
  <Modal v-if="confirmState.open" :title="confirmState.title" @close="answer(false)">
    <p v-if="confirmState.body" style="margin: 0" class="muted">{{ confirmState.body }}</p>
    <template #foot>
      <span class="grow"></span>
      <button class="btn" @click="answer(false)">Cancel</button>
      <button class="btn" :class="confirmState.danger ? 'danger-solid' : 'primary'" @click="answer(true)">{{ confirmState.action }}</button>
    </template>
  </Modal>
</template>
