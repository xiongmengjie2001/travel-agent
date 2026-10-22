<script setup lang="ts">
/**
 * Toast 队列（对应 docs/module-sdd/frontend.md §6）。
 *
 * 只负责展示与关闭；入队、5 条上限与自动消失计时全部由 chat store 负责。
 */
import type { ToastItem, ToastType } from '../types'

defineProps<{
  toasts: ToastItem[]
}>()

const emit = defineEmits<{
  dismiss: [id: number]
}>()

const ICONS: Record<ToastType, string> = {
  success: '✓',
  error: '✕',
  warning: '!',
  info: 'i',
}
</script>

<template>
  <Teleport to="body">
    <div class="toast-stack" aria-live="polite">
      <TransitionGroup name="toast">
        <div
          v-for="toast in toasts"
          :key="toast.id"
          class="toast"
          :class="`toast--${toast.type}`"
          role="status"
        >
          <span class="toast__icon" aria-hidden="true">{{ ICONS[toast.type] }}</span>
          <p class="toast__text">{{ toast.text }}</p>
          <button
            class="toast__close"
            type="button"
            aria-label="关闭通知"
            @click="emit('dismiss', toast.id)"
          >
            ×
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 120;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(360px, calc(100vw - 32px));
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  pointer-events: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left-width: 4px;
  border-radius: 10px;
  box-shadow: 0 8px 24px rgb(16 24 40 / 16%);
}

.toast__icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  border-radius: 50%;
}

.toast__text {
  flex: 1;
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
  word-break: break-word;
}

.toast__close {
  flex: none;
  padding: 0 2px;
  font-size: 16px;
  line-height: 1;
  color: var(--text-muted);
  cursor: pointer;
  background: none;
  border: 0;
}

.toast__close:hover {
  color: var(--text);
}

.toast--success {
  border-left-color: var(--success);
}

.toast--success .toast__icon {
  color: var(--success-text);
  background: var(--success-surface);
}

.toast--error {
  border-left-color: var(--danger);
}

.toast--error .toast__icon {
  color: var(--danger-text);
  background: var(--danger-surface);
}

.toast--warning {
  border-left-color: var(--warning-border);
}

.toast--warning .toast__icon {
  color: var(--warning-text);
  background: var(--warning-surface);
}

.toast--info {
  border-left-color: var(--primary);
}

.toast--info .toast__icon {
  color: var(--primary-deep);
  background: var(--primary-soft);
}

.toast-enter-active,
.toast-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(12px);
}
</style>
