<script setup lang="ts">
/**
 * 顶部横幅（对应 docs/module-sdd/frontend.md §5.3 / §5.4）。
 *
 * - 红色：网络失败，常驻并提供重试；
 * - 黄色：模板降级，按 `duration` 自动消失；
 * - 蓝色：一般信息。
 * 横幅内容与重试动作由 chat store 决定，组件只负责计时与转发事件。
 */
import { computed, onBeforeUnmount, watch } from 'vue'

import type { Banner } from '../types'

const props = defineProps<{
  banner: Banner | null
}>()

const emit = defineEmits<{
  dismiss: []
  retry: []
}>()

let timer: number | null = null

function clearTimer(): void {
  if (timer !== null) {
    window.clearTimeout(timer)
    timer = null
  }
}

watch(
  () => props.banner,
  (banner) => {
    clearTimer()
    if (banner?.duration && banner.duration > 0) {
      timer = window.setTimeout(() => emit('dismiss'), banner.duration)
    }
  },
  { immediate: true },
)

onBeforeUnmount(clearTimer)

const retryLabel = computed(() => {
  if (props.banner?.retry === 'history') return '刷新历史'
  if (props.banner?.retry === 'delete') return '重试删除'
  return '重试发送'
})
</script>

<template>
  <div v-if="banner" class="banner" :class="`banner--${banner.type}`" role="alert">
    <span class="banner__text">{{ banner.text }}</span>
    <div class="banner__actions">
      <button
        v-if="banner.retry"
        class="banner__button"
        type="button"
        @click="emit('retry')"
      >
        {{ retryLabel }}
      </button>
      <button
        class="banner__close"
        type="button"
        aria-label="关闭横幅"
        @click="emit('dismiss')"
      >
        ×
      </button>
    </div>
  </div>
</template>

<style scoped>
.banner {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 20px;
  font-size: 13px;
  border-bottom: 1px solid transparent;
}

.banner--error {
  color: var(--danger-text);
  background: var(--danger-surface);
  border-bottom-color: var(--danger-border);
}

.banner--warning {
  color: var(--warning-text);
  background: var(--warning-surface);
  border-bottom-color: var(--warning-border);
}

.banner--info {
  color: var(--primary-deep);
  background: var(--primary-soft);
  border-bottom-color: var(--primary-border);
}

.banner__text {
  min-width: 0;
  word-break: break-word;
}

.banner__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 6px;
}

.banner__button {
  padding: 3px 12px;
  font-size: 12px;
  color: inherit;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid currentcolor;
  border-radius: 6px;
}

.banner__button:hover {
  background: var(--surface-subtle);
}

.banner__close {
  width: 24px;
  height: 24px;
  font-size: 17px;
  line-height: 1;
  color: inherit;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: 6px;
  opacity: 0.7;
}

.banner__close:hover {
  background: rgb(0 0 0 / 6%);
  opacity: 1;
}
</style>
