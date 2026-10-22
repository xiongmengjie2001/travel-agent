<script setup lang="ts">
/**
 * 行程卡片（对应 docs/module-sdd/frontend.md §6）。
 *
 * 只接收后端裁好的 `TripCard`，不消费 `TripPlan`。逐日日程用折叠面板承载：
 * 折叠态一行摘要足够扫读，展开态才展示景点与花费明细。
 */
import { computed, ref } from 'vue'

import type { TripCard as TripCardModel, WeatherInfo } from '../types'
import CostBreakdown from './CostBreakdown.vue'
import WeatherForecast from './WeatherForecast.vue'

const props = defineProps<{
  trip: TripCardModel
}>()

const emit = defineEmits<{
  delete: [tripId: string]
}>()

/** 默认展开第 1 天：整卡全折叠时用户看不到任何日程内容，需先点一次才知情。 */
const expanded = ref<Set<number>>(new Set([1]))

function toggle(dayIndex: number): void {
  const next = new Set(expanded.value)
  if (next.has(dayIndex)) {
    next.delete(dayIndex)
  } else {
    next.add(dayIndex)
  }
  expanded.value = next
}

/** 逐日天气标签文案；后端在天气工具失败时会把 `weather` 置为 null（AC-5）。 */
function weatherLabel(weather: WeatherInfo | null): string {
  if (!weather) return '天气暂无'
  const { condition, temp_high, temp_low } = weather
  if (temp_high === null || temp_low === null) return condition
  return `${condition} ${temp_low}~${temp_high}℃`
}

/** `trip_id` 为空串表示该卡片不可操作（如尚未落库），此时不渲染删除按钮。 */
const canDelete = computed(() => Boolean(props.trip.trip_id))

function onDelete(): void {
  if (!canDelete.value) return
  const ok = window.confirm(`确认删除「${props.trip.destination}」的这条行程？删除后不可恢复。`)
  if (ok) emit('delete', props.trip.trip_id)
}
</script>

<template>
  <article class="trip-card">
    <header class="trip-card__head">
      <div class="trip-card__title">
        <h3>{{ trip.destination }}</h3>
        <span v-if="trip.source === 'template'" class="trip-card__badge">模板降级</span>
        <span class="trip-card__days">共 {{ trip.days }} 天</span>
      </div>
      <div class="trip-card__meta">
        <span class="trip-card__cost">预估 ¥{{ trip.total_cost }}</span>
        <button v-if="canDelete" class="trip-card__delete" type="button" @click="onDelete">
          删除
        </button>
      </div>
    </header>

    <p v-if="trip.degraded" class="trip-card__degraded">
      本条为降级模板方案（大模型不可用时的兜底），内容可供参考
    </p>

    <p v-if="!trip.daily.length" class="trip-card__empty">暂无逐日日程</p>

    <ul v-else class="trip-card__days-list">
      <li v-for="day in trip.daily" :key="day.day_index" class="day">
        <button class="day__head" type="button" @click="toggle(day.day_index)">
          <span class="day__caret">{{ expanded.has(day.day_index) ? '▾' : '▸' }}</span>
          <span class="day__index">第 {{ day.day_index }} 天</span>
          <span v-if="day.date" class="day__date">{{ day.date }}</span>
          <span class="day__weather">{{ weatherLabel(day.weather) }}</span>
          <span class="day__cost">¥{{ day.estimated_cost }}</span>
        </button>

        <div v-if="expanded.has(day.day_index)" class="day__body">
          <p v-if="!day.spots.length" class="day__no-spot">当日无景点安排</p>
          <ol v-else class="day__spots">
            <li v-for="spot in day.spots" :key="spot.name" class="spot">
              <span class="spot__name">{{ spot.name }}</span>
              <span class="spot__duration">{{ spot.duration_min }} 分钟</span>
            </li>
          </ol>
        </div>
      </li>
    </ul>
    <WeatherForecast :report="trip.weather_report" />
    <CostBreakdown :cost="trip.cost_breakdown" />
  </article>
</template>

<style scoped>
.trip-card {
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-card);
}

.trip-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.trip-card__title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

.trip-card__title h3 {
  margin: 0;
  font-size: 16px;
  color: var(--text);
}

.trip-card__days {
  font-size: 12px;
  color: var(--text-secondary);
}

.trip-card__badge {
  padding: 1px 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--warning-text);
  background: var(--warning-surface);
  border: 1px solid var(--warning-border);
  border-radius: 999px;
}

.trip-card__meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.trip-card__cost {
  font-size: 13px;
  font-weight: 600;
  color: var(--danger);
}

.trip-card__delete {
  padding: 4px 10px;
  font-size: 12px;
  color: var(--danger);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--danger-border);
  border-radius: 6px;
}

.trip-card__delete:hover {
  background: var(--danger-surface);
}

.trip-card__degraded {
  padding: 6px 10px;
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--warning-text);
  background: var(--warning-surface);
  border-left: 3px solid var(--warning-border);
  border-radius: 4px;
}

.trip-card__empty {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--text-muted);
}

.trip-card__days-list {
  padding: 0;
  margin: 10px 0 0;
  list-style: none;
}

.day + .day {
  border-top: 1px solid var(--surface-muted);
}

.day__head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 4px;
  font: inherit;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}

.day__head:hover {
  background: var(--surface-subtle);
}

.day__caret {
  width: 12px;
  color: var(--text-muted);
}

.day__index {
  font-weight: 600;
}

.day__date {
  color: var(--text-secondary);
}

.day__weather {
  padding: 1px 8px;
  font-size: 12px;
  color: var(--primary);
  background: var(--primary-soft);
  border-radius: 10px;
}

.day__cost {
  margin-left: auto;
  color: var(--text-secondary);
}

.day__body {
  padding: 2px 4px 10px 30px;
}

.day__no-spot {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
}

.day__spots {
  padding-left: 18px;
  margin: 0;
}

.spot {
  display: flex;
  gap: 10px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--text-secondary);
}

.spot__duration {
  color: var(--text-muted);
}
</style>
