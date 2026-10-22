<script setup lang="ts">
/**
 * 主对话区（对应 docs/module-sdd/frontend.md §6）。
 *
 * 只做组装：消息列表 + 输入框；业务动作全部经 emit 交给 App。
 */
import type { ChatMessage } from '../types'
import ChatInput from './ChatInput.vue'
import MessageList from './MessageList.vue'

withDefaults(
  defineProps<{
    messages: ChatMessage[]
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{
  send: [text: string, startDate: string | null]
  'delete-trip': [tripId: string]
}>()

function onSend(text: string, startDate: string | null): void {
  emit('send', text, startDate)
}
</script>

<template>
  <section class="chat-panel">
    <MessageList
      :messages="messages"
      :loading="loading"
      @delete-trip="emit('delete-trip', $event)"
    />
    <footer class="chat-panel__footer">
      <ChatInput :disabled="loading" @send="onSend" />
    </footer>
  </section>
</template>

<style scoped>
.chat-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--surface);
}

.chat-panel__footer {
  flex: none;
  padding: 14px 24px;
  background: var(--surface);
  border-top: 1px solid var(--border);
}
</style>
