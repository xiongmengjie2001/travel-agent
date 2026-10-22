<script setup lang="ts">
/**
 * 历史行程侧边栏（对应 docs/module-sdd/frontend.md §6）。
 *
 * 展示概要列表；点击加载完整详情到主对话区，删除 / 刷新经 emit 交给 App。
 * 组件不发起请求，也不解析 action。
 */
import type { TripCard } from '../types'

withDefaults(
  defineProps<{
    trips: TripCard[]
    activeTripId?: string | null
    loading?: boolean
    detailLoading?: boolean
    errorMessage?: string | null
  }>(),
  {
    activeTripId: null,
    loading: false,
    detailLoading: false,
    errorMessage: null,
  },
)

const emit = defineEmits<{
  select: [tripId: string]
  delete: [tripId: string]
  refresh: []
}>()

function onDelete(trip: TripCard): void {
  const ok = window.confirm(`确认删除「${trip.destination}」的这条行程？删除后不可恢复。`)
  if (ok) emit('delete', trip.trip_id)
}
</script>

<template>
  <aside class="history-sidebar">
    <header class="history-sidebar__head">
      <div>
        <h2 class="history-sidebar__title">历史行程</h2>
        <p class="history-sidebar__count">共 {{ trips.length }} 条</p>
      </div>
      <button
        class="history-sidebar__refresh"
        type="button"
        :disabled="loading"
        @click="emit('refresh')"
      >
        刷新
      </button>
    </header>

    <p v-if="detailLoading" class="history-sidebar__detail-loading">正在加载行程详情…</p>
    <p v-if="errorMessage" class="history-sidebar__error">{{ errorMessage }}</p>

    <div v-if="loading && !trips.length" class="history-sidebar__state">加载中…</div>

    <div v-else-if="!trips.length" class="history-sidebar__state">
      <p class="history-sidebar__empty-title">暂无历史行程</p>
      <p class="history-sidebar__empty-hint">发送一条出行需求，生成的行程会自动出现在这里。</p>
    </div>

    <ul v-else class="history-sidebar__list">
      <li
        v-for="trip in trips"
        :key="trip.trip_id || `${trip.destination}-${trip.days}`"
        class="history-item"
        :class="{ 'history-item--active': trip.trip_id === activeTripId }"
      >
        <button
          class="history-item__main"
          type="button"
          :disabled="detailLoading"
          @click="emit('select', trip.trip_id)"
        >
          <span class="history-item__destination">{{ trip.destination }}</span>
          <span class="history-item__meta">
            {{ trip.days }} 天 · ¥{{ trip.total_cost }}
          </span>
          <span v-if="trip.degraded" class="history-item__tag">模板方案</span>
        </button>
        <button
          class="history-item__delete"
          type="button"
          aria-label="删除行程"
          @click="onDelete(trip)"
        >
          删除
        </button>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.history-sidebar {
  display: flex;
  flex: none;
  flex-direction: column;
  width: 280px;
  min-height: 0;
  background: var(--surface-subtle);
  border-right: 1px solid var(--border);
}

.history-sidebar__head {
  display: flex;
  flex: none;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--border);
}

.history-sidebar__title {
  margin: 0;
  font-size: 14px;
  color: var(--text);
}

.history-sidebar__count {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}

.history-sidebar__refresh {
  padding: 4px 10px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
}

.history-sidebar__refresh:hover:not(:disabled) {
  color: var(--primary);
  border-color: var(--primary-border);
}

.history-sidebar__refresh:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.history-sidebar__detail-loading,
.history-sidebar__error {
  margin: 8px 16px 0;
  font-size: 12px;
}

.history-sidebar__detail-loading {
  color: var(--primary);
}

.history-sidebar__error {
  color: var(--danger-text);
}

.history-sidebar__state {
  padding: 28px 16px;
  font-size: 13px;
  color: var(--text-muted);
  text-align: center;
}

.history-sidebar__empty-title {
  margin: 0;
  color: var(--text-secondary);
}

.history-sidebar__empty-hint {
  margin: 6px 0 0;
  line-height: 1.6;
}

.history-sidebar__list {
  flex: 1;
  padding: 8px;
  margin: 0;
  overflow-y: auto;
  list-style: none;
}

.history-item {
  display: flex;
  align-items: stretch;
  border-radius: 8px;
}

.history-item + .history-item {
  margin-top: 4px;
}

.history-item:hover,
.history-item--active {
  background: var(--surface);
  box-shadow: inset 0 0 0 1px var(--border);
}

.history-item--active {
  box-shadow: inset 0 0 0 1px var(--primary-border);
}

.history-item__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 10px 12px;
  font: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: 8px;
}

.history-item__main:disabled {
  cursor: progress;
}

.history-item__destination {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-item__meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.history-item__tag {
  align-self: flex-start;
  padding: 0 6px;
  margin-top: 2px;
  font-size: 11px;
  color: var(--warning-text);
  background: var(--warning-surface);
  border-radius: 4px;
}

.history-item__delete {
  flex: none;
  padding: 0 10px;
  font-size: 12px;
  color: var(--danger);
  cursor: pointer;
  background: none;
  border: 0;
  border-left: 1px solid var(--border);
  border-radius: 0 8px 8px 0;
}

.history-item__delete:hover {
  background: var(--danger-surface);
}
</style>
