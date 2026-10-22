<script setup lang="ts">
/**
 * 多日天气（对应 docs/module-sdd/frontend.md §6）。
 *
 * 直接渲染后端 `TripCard.weather_report`：日期、天气状况、温度区间。
 * 天气工具失败时后端会给空数组，此时整块不渲染。
 */
import type { WeatherInfo } from '../types'

defineProps<{
  report: WeatherInfo[]
}>()

/** 压缩为 MM-DD，完整日期保留在 title 中。 */
function shortDate(date: string): string {
  const matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  return matched ? `${matched[2]}-${matched[3]}` : date
}

/** 温度缺失时按实际可用字段展示，避免出现 `null~24℃`。 */
function temperature(weather: WeatherInfo): string {
  const { temp_high: high, temp_low: low } = weather
  if (high === null && low === null) return '温度暂无'
  if (high === null) return `最低 ${low}℃`
  if (low === null) return `最高 ${high}℃`
  return `${low}~${high}℃`
}
</script>

<template>
  <section v-if="report.length" class="weather">
    <h4 class="weather__title">多日天气</h4>
    <ul class="weather__list">
      <li v-for="item in report" :key="`${item.date}-${item.condition}`" class="weather__item">
        <span class="weather__date" :title="item.date">{{ shortDate(item.date) }}</span>
        <span class="weather__condition">{{ item.condition }}</span>
        <span class="weather__temp">{{ temperature(item) }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.weather {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--surface-muted);
}

.weather__title {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.weather__list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.weather__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: 999px;
}

.weather__date {
  font-weight: 600;
  color: var(--text);
}

.weather__condition {
  color: var(--primary);
}

.weather__temp {
  color: var(--text-muted);
}
</style>
