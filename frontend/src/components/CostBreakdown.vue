<script setup lang="ts">
/**
 * 分项花费（对应 docs/module-sdd/frontend.md §6）。
 *
 * 渲染后端 `TripCard.cost_breakdown`：门票 / 市内交通 / 住宿 / 餐饮 / 总计。
 * 后端未提供分项（`null`）时给出明确占位，避免整块静默消失。
 */
import { computed } from 'vue'

import type { CostResult } from '../types'

const props = defineProps<{
  cost: CostResult | null
}>()

const rows = computed(() => {
  const cost = props.cost
  if (!cost) return []
  return [
    { label: '门票', value: cost.tickets_total },
    { label: '市内交通', value: cost.transport_total },
    { label: '住宿', value: cost.lodging_total },
    { label: '餐饮', value: cost.food_total },
  ]
})

/** `null` 表示后端没有预算信息，此时不展示预算结论。 */
const budgetLabel = computed(() => {
  const cost = props.cost
  if (!cost || cost.within_budget === null) return null
  return cost.within_budget ? '在预算内' : '超出预算'
})
</script>

<template>
  <section class="cost">
    <h4 class="cost__title">分项花费</h4>

    <p v-if="!cost" class="cost__empty">暂无分项花费</p>

    <template v-else>
      <dl class="cost__rows">
        <div v-for="row in rows" :key="row.label" class="cost__row">
          <dt class="cost__label">{{ row.label }}</dt>
          <dd class="cost__value">¥{{ row.value }}</dd>
        </div>
        <div class="cost__row cost__row--total">
          <dt class="cost__label">总计</dt>
          <dd class="cost__value">¥{{ cost.total }}</dd>
        </div>
      </dl>

      <span
        v-if="budgetLabel"
        class="cost__budget"
        :class="{ 'cost__budget--over': cost.within_budget === false }"
      >
        {{ budgetLabel }}
      </span>
    </template>
  </section>
</template>

<style scoped>
.cost {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--surface-muted);
}

.cost__title {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.cost__empty {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}

.cost__rows {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  margin: 0;
}

.cost__row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
}

.cost__label {
  color: var(--text-muted);
}

.cost__value {
  margin: 0;
  color: var(--text);
}

.cost__row--total .cost__label,
.cost__row--total .cost__value {
  font-weight: 600;
  color: var(--danger);
}

.cost__budget {
  display: inline-block;
  padding: 1px 8px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--success-text);
  background: var(--success-surface);
  border: 1px solid var(--success-border);
  border-radius: 999px;
}

.cost__budget--over {
  color: var(--danger-text);
  background: var(--danger-surface);
  border-color: var(--danger-border);
}
</style>
