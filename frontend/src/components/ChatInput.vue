<script setup lang="ts">
/**
 * 对话输入框（对应 docs/module-sdd/frontend.md §6）。
 *
 * - 回车发送、Shift+Enter 换行、发送后清空；
 * - `disabled` 由父组件在请求进行中置为 true，防重复提交；
 * - 快捷提问一键发送；
 * - 出发日期随 send 事件透传给 store，再由 API 层放进 `start_date`。
 */
import { computed, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  send: [text: string, startDate: string | null]
}>()

const QUICK_PROMPTS = [
  '帮我规划北京3日游',
  '查看我的行程',
  '搜索成都的行程',
  '讲个笑话',
] as const

const draft = ref('')
const startDate = ref('')

/** 纯空白输入不发 —— 后端也会拒（1001），没必要走一趟网络。 */
const canSend = computed(() => !props.disabled && draft.value.trim().length > 0)

function emitSend(text: string): void {
  emit('send', text, startDate.value || null)
}

function submit(): void {
  if (!canSend.value) return
  const text = draft.value.trim()
  draft.value = ''
  emitSend(text)
}

function usePrompt(prompt: string): void {
  if (props.disabled) return
  emitSend(prompt)
}

/** 回车提交；Shift+Enter 交给默认行为（换行）。输入法组合中的回车不提交。 */
function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey) return
  if (event.isComposing) return
  event.preventDefault()
  submit()
}
</script>

<template>
  <div class="chat-input">
    <div class="chat-input__quick">
      <button
        v-for="prompt in QUICK_PROMPTS"
        :key="prompt"
        class="chat-input__quick-item"
        type="button"
        :disabled="disabled"
        @click="usePrompt(prompt)"
      >
        {{ prompt }}
      </button>
    </div>

    <form class="chat-input__form" @submit.prevent="submit">
      <textarea
        v-model="draft"
        class="chat-input__box"
        rows="2"
        placeholder="说说你的出行计划，例如「帮我规划去北京玩 3 天」；Enter 发送，Shift+Enter 换行"
        :disabled="disabled"
        @keydown="onKeydown"
      />

      <div class="chat-input__actions">
        <label class="chat-input__date">
          <span class="chat-input__date-label">出发日期</span>
          <input v-model="startDate" class="chat-input__date-input" type="date" :disabled="disabled" />
        </label>
        <button class="chat-input__send" type="submit" :disabled="!canSend">
          {{ disabled ? '规划中…' : '发送' }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chat-input__quick {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chat-input__quick-item {
  padding: 4px 12px;
  font-size: 12px;
  color: var(--primary);
  cursor: pointer;
  background: var(--primary-soft);
  border: 1px solid var(--primary-border);
  border-radius: 999px;
}

.chat-input__quick-item:hover:not(:disabled) {
  background: var(--primary-soft-hover);
}

.chat-input__quick-item:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
  background: var(--surface-muted);
  border-color: var(--border);
}

.chat-input__form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chat-input__box {
  width: 100%;
  padding: 10px 12px;
  font: inherit;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  resize: none;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  outline: none;
}

.chat-input__box:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-ring);
}

.chat-input__box:disabled {
  color: var(--text-muted);
  background: var(--surface-subtle);
}

.chat-input__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.chat-input__date {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.chat-input__date-input {
  padding: 5px 8px;
  font: inherit;
  font-size: 12px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
}

.chat-input__send {
  height: 38px;
  padding: 0 22px;
  font-size: 14px;
  color: #fff;
  cursor: pointer;
  background: var(--primary);
  border: 0;
  border-radius: 10px;
}

.chat-input__send:hover:not(:disabled) {
  background: var(--primary-hover);
}

.chat-input__send:disabled {
  cursor: not-allowed;
  background: var(--primary-disabled);
}
</style>
