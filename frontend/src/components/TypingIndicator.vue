<script setup lang="ts">
/**
 * 加载中打字指示器（对应 docs/module-sdd/frontend.md §6）。
 *
 * 纯展示组件，无 props / emits；由 MessageList 在 loading 时渲染。
 */
</script>

<template>
  <div class="typing" role="status" aria-label="智能体正在输入">
    <span class="typing__dot" />
    <span class="typing__dot" />
    <span class="typing__dot" />
  </div>
</template>

<style scoped>
.typing {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 12px 16px;
  background: var(--surface-muted);
  border-radius: 12px;
  border-bottom-left-radius: 4px;
  width: fit-content;
}

.typing__dot {
  width: 6px;
  height: 6px;
  background: var(--text-muted);
  border-radius: 50%;
  animation: typing-bounce 1.2s infinite ease-in-out;
}

.typing__dot:nth-child(2) {
  animation-delay: 0.15s;
}

.typing__dot:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes typing-bounce {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.45;
  }

  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}
</style>
