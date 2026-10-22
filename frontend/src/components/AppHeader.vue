<script setup lang="ts">
/**
 * 顶部栏（对应 docs/module-sdd/frontend.md §6）。
 *
 * Logo / 产品标题 / 设置入口 / 主题切换。纯展示组件，主题与设置动作由 App 接线。
 */
import { computed } from 'vue'

import type { ThemeMode } from '../types'

const props = defineProps<{
  theme: ThemeMode
}>()

const emit = defineEmits<{
  'toggle-theme': []
  'open-settings': []
}>()

const themeLabel = computed(() => {
  if (props.theme === 'dark') return '切换浅色'
  if (props.theme === 'light') return '切换深色'
  return '切换主题'
})
</script>

<template>
  <header class="app-header">
    <div class="app-header__brand">
      <span class="app-header__logo" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
          <path
            d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5L21 16Z"
            fill="currentColor"
          />
        </svg>
      </span>
      <div class="app-header__titles">
        <h1 class="app-header__title">旅行规划智能体</h1>
        <p class="app-header__subtitle">描述出行需求，自动生成行程方案</p>
      </div>
    </div>

    <div class="app-header__actions">
      <button class="app-header__button" type="button" @click="emit('toggle-theme')">
        {{ themeLabel }}
      </button>
      <button class="app-header__button" type="button" @click="emit('open-settings')">
        设置
      </button>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.app-header__brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.app-header__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  color: #fff;
  background: linear-gradient(135deg, var(--primary), var(--primary-deep));
  border-radius: 10px;
}

.app-header__titles {
  min-width: 0;
}

.app-header__title {
  margin: 0;
  font-size: 17px;
  color: var(--text);
}

.app-header__subtitle {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.app-header__actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.app-header__button {
  padding: 7px 14px;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
}

.app-header__button:hover {
  color: var(--primary);
  background: var(--surface-subtle);
  border-color: var(--primary-border);
}
</style>
