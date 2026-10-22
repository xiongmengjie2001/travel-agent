<script setup lang="ts">
/**
 * 用户消息气泡（对应 docs/module-sdd/frontend.md §6）。
 *
 * 只负责展示，不判断内容；输入文本已由 store 去空白。
 */
defineProps<{
  text: string
}>()
</script>

<template>
  <div class="user-message">
    <div class="user-message__bubble">
      <p class="user-message__text">{{ text }}</p>
    </div>
  </div>
</template>

<style scoped>
.user-message {
  display: flex;
  justify-content: flex-end;
}

.user-message__bubble {
  max-width: 78%;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.6;
  color: #fff;
  word-break: break-word;
  background: var(--primary);
  border-radius: 12px;
  border-bottom-right-radius: 4px;
}

.user-message__text {
  margin: 0;
  white-space: pre-wrap;
}
</style>
