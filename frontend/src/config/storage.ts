/**
 * 本地配置读写（对应 docs/module-sdd/frontend.md §5.1）。
 *
 * 单独成模块，让 API 层可以读取后端地址而不必反向 import Pinia store。
 * 所有读取都做逐字段校验；localStorage 不可用时回落到默认值。
 */

import type { AppConfig, ThemeMode } from '../types'

export const CONFIG_STORAGE_KEY = 'travel-agent2:config'
export const DEFAULT_MODEL = 'deepseek-v4-flash'

/** 开发期默认留空，请求走 vite.config.ts 的 `/api` 代理。 */
export const DEFAULT_API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export const DEFAULT_CONFIG: AppConfig = {
  theme: 'light',
  apiKey: '',
  model: DEFAULT_MODEL,
  apiBaseUrl: normalizeApiBaseUrl(DEFAULT_API_BASE_URL),
}

const THEME_MODES: readonly ThemeMode[] = ['light', 'dark', 'system']

function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && (THEME_MODES as readonly string[]).includes(value)
}

/** 去掉首尾空白与末尾斜杠，避免与 `/api/...` 拼接出双斜杠。 */
export function normalizeApiBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, '')
}

/** 安全取得 localStorage；隐私模式或权限受限时返回 null。 */
export function getStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

/** 读取并校验配置；任何异常都回落到默认值。 */
export function readConfig(): AppConfig {
  const storage = getStorage()
  if (!storage) return { ...DEFAULT_CONFIG }

  try {
    const raw = storage.getItem(CONFIG_STORAGE_KEY)
    if (!raw) return { ...DEFAULT_CONFIG }

    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return { ...DEFAULT_CONFIG }

    const value = parsed as Partial<Record<keyof AppConfig, unknown>>
    return {
      theme: isThemeMode(value.theme) ? value.theme : DEFAULT_CONFIG.theme,
      apiKey: typeof value.apiKey === 'string' ? value.apiKey : DEFAULT_CONFIG.apiKey,
      model:
        typeof value.model === 'string' && value.model.trim()
          ? value.model
          : DEFAULT_CONFIG.model,
      apiBaseUrl:
        typeof value.apiBaseUrl === 'string'
          ? normalizeApiBaseUrl(value.apiBaseUrl)
          : DEFAULT_CONFIG.apiBaseUrl,
    }
  } catch {
    return { ...DEFAULT_CONFIG }
  }
}

/** 写回配置；写入失败只告警，不影响页面。 */
export function persistConfig(config: AppConfig): void {
  const storage = getStorage()
  if (!storage) return
  try {
    storage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config))
  } catch (error) {
    console.warn('保存前端配置失败', error)
  }
}

/** 清除持久化配置；失败只告警。 */
export function removeConfig(): void {
  const storage = getStorage()
  if (!storage) return
  try {
    storage.removeItem(CONFIG_STORAGE_KEY)
  } catch (error) {
    console.warn('清除前端配置失败', error)
  }
}
