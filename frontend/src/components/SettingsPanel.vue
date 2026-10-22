<script setup lang="ts">
/**
 * 设置面板（对应 docs/module-sdd/frontend.md §6）。
 *
 * 编辑主题 / API Key / 模型。组件不直接读写 store，全部经 props / emits
 * 交给 App 接线；关闭支持遮罩点击、关闭按钮与 Esc。
 */
import { onBeforeUnmount, watch } from 'vue'

import type { ThemeMode } from '../types'

const props = defineProps<{
  open: boolean
  theme: ThemeMode
  apiKey: string
  model: string
  apiBaseUrl: string
}>()

const emit = defineEmits<{
  close: []
  'update:theme': [value: ThemeMode]
  'update:apiKey': [value: string]
  'update:model': [value: string]
  'update:apiBaseUrl': [value: string]
}>()

function onThemeChange(event: Event): void {
  emit('update:theme', (event.target as HTMLSelectElement).value as ThemeMode)
}

function onApiKeyInput(event: Event): void {
  emit('update:apiKey', (event.target as HTMLInputElement).value)
}

function onModelInput(event: Event): void {
  emit('update:model', (event.target as HTMLInputElement).value)
}

function onApiBaseUrlInput(event: Event): void {
  emit('update:apiBaseUrl', (event.target as HTMLInputElement).value)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.open) emit('close')
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      window.addEventListener('keydown', onKeydown)
    } else {
      window.removeEventListener('keydown', onKeydown)
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="settings" @click.self="emit('close')">
      <section class="settings__panel" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <header class="settings__head">
          <h2 id="settings-title" class="settings__title">设置</h2>
          <button class="settings__close" type="button" aria-label="关闭设置" @click="emit('close')">
            ×
          </button>
        </header>

        <div class="settings__body">
          <label class="settings__field">
            <span class="settings__label">主题</span>
            <select class="settings__control" :value="theme" @change="onThemeChange">
              <option value="light">浅色</option>
              <option value="dark">深色</option>
              <option value="system">跟随系统</option>
            </select>
          </label>

          <label class="settings__field">
            <span class="settings__label">API Key</span>
            <input
              class="settings__control"
              type="password"
              autocomplete="off"
              placeholder="sk-..."
              :value="apiKey"
              @input="onApiKeyInput"
            />
          </label>

          <label class="settings__field">
            <span class="settings__label">模型</span>
            <input
              class="settings__control"
              type="text"
              :value="model"
              @input="onModelInput"
            />
          </label>

          <label class="settings__field">
            <span class="settings__label">后端地址</span>
            <input
              class="settings__control"
              type="text"
              placeholder="如 http://127.0.0.1:8000；留空走 /api 代理"
              :value="apiBaseUrl"
              @input="onApiBaseUrlInput"
            />
          </label>

          <p class="settings__note">
            配置仅保存在当前浏览器的 localStorage，不会上传到后端或写入仓库。后端地址留空时
            使用开发服务器的 /api 代理；后端实际使用的 DeepSeek Key 仍由服务端环境变量决定。
          </p>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.settings {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgb(16 24 40 / 45%);
}

.settings__panel {
  width: min(440px, 100%);
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 18px 48px rgb(16 24 40 / 24%);
}

.settings__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}

.settings__title {
  margin: 0;
  font-size: 16px;
  color: var(--text);
}

.settings__close {
  width: 28px;
  height: 28px;
  font-size: 20px;
  line-height: 1;
  color: var(--text-secondary);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: 6px;
}

.settings__close:hover {
  background: var(--surface-muted);
}

.settings__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
}

.settings__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.settings__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.settings__control {
  width: 100%;
  padding: 9px 12px;
  font: inherit;
  font-size: 14px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  outline: none;
}

.settings__control:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-ring);
}

.settings__note {
  padding: 10px 12px;
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-muted);
  background: var(--surface-subtle);
  border-radius: 8px;
}
</style>
