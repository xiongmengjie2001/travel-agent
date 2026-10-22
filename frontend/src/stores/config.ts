/**
 * 配置状态（对应 docs/module-sdd/frontend.md §5.1）。
 *
 * 主题 / API Key / 模型 / 后端地址仅保存在当前浏览器的 localStorage，
 * 不上行到后端。每次修改立即写回，避免关闭设置后马上发送时读到旧值。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  CONFIG_STORAGE_KEY,
  DEFAULT_CONFIG,
  DEFAULT_MODEL,
  persistConfig,
  readConfig,
  removeConfig,
} from '../config/storage'
import type { AppConfig, ThemeMode } from '../types'

export { CONFIG_STORAGE_KEY, DEFAULT_CONFIG, DEFAULT_MODEL, readConfig }

/** 把主题同步到根元素；system 表示交给 prefers-color-scheme。 */
export function applyTheme(theme: ThemeMode): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (theme === 'system') {
    delete root.dataset.theme
  } else {
    root.dataset.theme = theme
  }
}

export const useConfigStore = defineStore('config', () => {
  const initial = readConfig()
  const theme = ref<ThemeMode>(initial.theme)
  const apiKey = ref(initial.apiKey)
  const model = ref(initial.model)
  const apiBaseUrl = ref(initial.apiBaseUrl)

  applyTheme(theme.value)

  /** 当前配置快照，供持久化使用。 */
  function snapshot(): AppConfig {
    return {
      theme: theme.value,
      apiKey: apiKey.value,
      model: model.value,
      apiBaseUrl: apiBaseUrl.value,
    }
  }

  function persist(): void {
    persistConfig(snapshot())
  }

  function setTheme(value: ThemeMode): void {
    theme.value = value
    applyTheme(theme.value)
    persist()
  }

  function toggleTheme(): void {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  function setApiKey(value: string): void {
    apiKey.value = value
    persist()
  }

  function setModel(value: string): void {
    model.value = value.trim() || DEFAULT_MODEL
    persist()
  }

  function setApiBaseUrl(value: string): void {
    // 输入过程中不做裁剪，避免用户刚敲下的 “/” 被立即吞掉；
    // 请求层读取配置时会调用 normalizeApiBaseUrl。
    apiBaseUrl.value = value
    persist()
  }

  function reset(): void {
    theme.value = DEFAULT_CONFIG.theme
    apiKey.value = DEFAULT_CONFIG.apiKey
    model.value = DEFAULT_CONFIG.model
    apiBaseUrl.value = DEFAULT_CONFIG.apiBaseUrl
    applyTheme(theme.value)
    removeConfig()
  }

  return {
    theme,
    apiKey,
    model,
    apiBaseUrl,
    setTheme,
    toggleTheme,
    setApiKey,
    setModel,
    setApiBaseUrl,
    reset,
  }
})
