<script setup lang="ts">
/**
 * 应用入口（对应 docs/module-sdd/frontend.md §6）。
 *
 * 只做组装与事件接线：状态全部来自 chat / config 两个 store，
 * 子组件不直接调用 API。布局为「顶部栏 + 历史侧边栏 + 主对话区」。
 */
import { storeToRefs } from 'pinia'
import { onMounted, ref } from 'vue'

import AppHeader from './components/AppHeader.vue'
import BannerBar from './components/BannerBar.vue'
import ChatPanel from './components/ChatPanel.vue'
import HistorySidebar from './components/HistorySidebar.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import ToastStack from './components/ToastStack.vue'
import { useChatStore } from './stores/chat'
import { useConfigStore } from './stores/config'

const chat = useChatStore()
const config = useConfigStore()

const {
  messages,
  loading,
  history,
  historyLoading,
  historyError,
  detailLoading,
  activeTripId,
  banner,
  toasts,
} = storeToRefs(chat)
const { theme, apiKey, model, apiBaseUrl } = storeToRefs(config)

const settingsOpen = ref(false)

function handleSend(text: string, startDate: string | null): void {
  void chat.send(text, startDate)
}

function handleDeleteTrip(tripId: string): void {
  void chat.deleteTrip(tripId)
}

function handleSelectTrip(tripId: string): void {
  void chat.openHistoryTrip(tripId)
}

function handleRefreshHistory(): void {
  void chat.refreshHistory(true)
}

onMounted(() => {
  void chat.loadHistory()
})
</script>

<template>
  <div class="app">
    <AppHeader
      :theme="theme"
      @toggle-theme="config.toggleTheme()"
      @open-settings="settingsOpen = true"
    />

    <BannerBar
      :banner="banner"
      @dismiss="chat.clearBanner()"
      @retry="chat.retryBanner()"
    />

    <div class="app__body">
      <HistorySidebar
        :trips="history"
        :active-trip-id="activeTripId"
        :loading="historyLoading"
        :detail-loading="detailLoading"
        :error-message="historyError?.message ?? null"
        @select="handleSelectTrip"
        @delete="handleDeleteTrip"
        @refresh="handleRefreshHistory"
      />

      <ChatPanel
        :messages="messages"
        :loading="loading"
        @send="handleSend"
        @delete-trip="handleDeleteTrip"
      />
    </div>

    <SettingsPanel
      :open="settingsOpen"
      :theme="theme"
      :api-key="apiKey"
      :model="model"
      :api-base-url="apiBaseUrl"
      @close="settingsOpen = false"
      @update:theme="config.setTheme"
      @update:api-key="config.setApiKey"
      @update:model="config.setModel"
      @update:api-base-url="config.setApiBaseUrl"
    />

    <ToastStack :toasts="toasts" @dismiss="chat.removeToast" />
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 1280px;
  margin: 0 auto;
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-app);
}

.app__body {
  display: flex;
  flex: 1;
  min-height: 0;
}

@media (max-width: 760px) {
  .app__body {
    flex-direction: column;
  }

  .history-sidebar {
    width: 100%;
    max-height: 220px;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }
}
</style>
