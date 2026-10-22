<script setup lang="ts">
/**
 * 消息列表（对应 docs/module-sdd/frontend.md §6）。
 *
 * 负责自动滚动到底部与按角色分派气泡；不发起任何请求。
 * 用户 / 助手（含错误）气泡分别由 UserMessage / AssistantMessage 渲染。
 */
import { nextTick, onMounted, ref, watch } from 'vue'

import type { ChatMessage } from '../types'
import AssistantMessage from './AssistantMessage.vue'
import TypingIndicator from './TypingIndicator.vue'
import UserMessage from './UserMessage.vue'

const props = withDefaults(
  defineProps<{
    messages: ChatMessage[]
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{
  'delete-trip': [tripId: string]
}>()

const listEl = ref<HTMLElement | null>(null)

function scrollToBottom(): void {
  void nextTick(() => {
    const element = listEl.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

watch(() => [props.messages.length, props.loading], scrollToBottom, { flush: 'post' })
onMounted(scrollToBottom)
</script>

<template>
  <div ref="listEl" class="message-list">
    <p v-if="!messages.length && !loading" class="message-list__hint">
      输入出行需求开始规划，例如「帮我规划去北京玩 3 天」
    </p>

    <template v-for="message in messages" :key="message.id">
      <UserMessage v-if="message.role === 'user'" :text="message.text" />
      <AssistantMessage v-else :message="message" @delete-trip="emit('delete-trip', $event)" />
    </template>

    <div v-if="loading" class="message-list__typing">
      <TypingIndicator />
    </div>
  </div>
</template>

<style scoped>
.message-list {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 20px 24px;
  overflow-y: auto;
}

.message-list__hint {
  margin: 0;
  padding: 24px 0;
  font-size: 13px;
  color: var(--text-muted);
  text-align: center;
}

.message-list__typing {
  display: flex;
  justify-content: flex-start;
}
</style>
