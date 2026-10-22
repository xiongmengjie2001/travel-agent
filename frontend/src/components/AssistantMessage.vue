<script setup lang="ts">
/**
 * 助手 / 错误消息气泡（对应 docs/module-sdd/frontend.md §6）。
 *
 * 正常助手消息与错误消息共用左侧布局，通过 role 样式区分：
 * `error` 用红色描边，与正常回复分开（1002 不是故障，但用户必须看见）。
 * 消息可携带一张 `trip` 卡片或一组 `trips` 卡片。
 */
import type { ChatMessage } from '../types'
import TripCard from './TripCard.vue'

defineProps<{
  message: ChatMessage
}>()

const emit = defineEmits<{
  'delete-trip': [tripId: string]
}>()
</script>

<template>
  <div class="assistant-message" :class="`assistant-message--${message.role}`">
    <div
      class="assistant-message__bubble"
      :class="{ 'assistant-message__bubble--with-card': message.trip || message.trips.length }"
    >
      <p v-if="message.text" class="assistant-message__text">{{ message.text }}</p>

      <TripCard
        v-if="message.trip"
        :trip="message.trip"
        @delete="emit('delete-trip', $event)"
      />

      <div v-if="message.trips.length" class="assistant-message__trips">
        <TripCard
          v-for="trip in message.trips"
          :key="trip.trip_id || `${trip.destination}-${trip.days}`"
          :trip="trip"
          @delete="emit('delete-trip', $event)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.assistant-message {
  display: flex;
  justify-content: flex-start;
}

.assistant-message__bubble {
  max-width: 78%;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  word-break: break-word;
  background: var(--surface-muted);
  border-radius: 12px;
  border-bottom-left-radius: 4px;
}

.assistant-message--error .assistant-message__bubble {
  color: var(--danger-text);
  background: var(--danger-surface);
  border: 1px solid var(--danger-border);
}

/* 卡片自带内边距，气泡在承载卡片时收窄留白，避免双重内边距。 */
.assistant-message__bubble--with-card {
  padding: 10px;
  background: var(--surface-card-wrap);
}

.assistant-message__text {
  margin: 0;
  white-space: pre-wrap;
}

.assistant-message__bubble--with-card .assistant-message__text {
  padding: 0 4px;
}

.assistant-message__trips {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 8px;
}
</style>
