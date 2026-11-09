<script setup lang="ts">
/* 一个可选的大脑（界面稿⑧屏左半的一行）。
 *
 * 右边那一格是 key。它有三种样子，对应三种真实状态，不合并：
 *   本地模型      —— 「本机 · 无需 key」，连输入框都不出现
 *   还没填         —— 一个 type="password" 的框
 *   这台浏览器有了 —— 掩码回显 + 换一个 / 忘掉
 *
 * 界面稿上还有「key 有了但当前 IP 没权限」这类状态，那要真发一次请求才知道
 * （S7）。这里不预言——一个猜出来的状态比没有状态更糟。
 *
 * 「正文额度」那一格带着来路（内置 / 估计 / 你填的 / 供应商）。估计的那一行
 * 连同「这本书」一格一起着黄檗：数是猜的，等你判断要不要填上真实窗口。
 *
 * 「模型」那一格本身就是换模型的开关（`qwen-plus ▾`），点一下弹出 `ModelPicker`。
 * 不另放一个「换模型」按钮：这张表和人格编辑器分一屏宽，多占的每一像素都是从
 * 「这本书」那一格里挤出来的（实测挤成了「整本装…」）。弹窗由 view 开，这里只报
 * 「我被点了」。
 */
import { computed, ref, watch } from 'vue'

import { maskKey } from '@/lib/brainKeys'
import { isImeEnter } from '@/lib/keys'
import { SOURCE_HINT, SOURCE_TAG } from '@/lib/contextSource'
import type { Brain } from '@/types'

const props = defineProps<{
  brain: Brain
  chosen: boolean
  savedKey: string
  isPicking: boolean
}>()

const emit = defineEmits<{
  choose: [name: string]
  save: [name: string, key: string]
  forget: [name: string]
  remove: [brain: Brain]
  'pick-model': [name: string]
}>()

const editing = ref(false)
const draft = ref('')

watch(
  () => props.savedKey,
  () => {
    editing.value = false
    // 草稿一律清空，绝不回填已存的 key：填进输入框就意味着它会被
    // 浏览器的表单历史、密码管理器、以及任何截屏看到
    draft.value = ''
  }
)

function commit() {
  emit('save', props.brain.name, draft.value)
  draft.value = ''
}

/** 回车就存；输入法上屏那一下回车不算（`lib/keys`） */
function commitOnEnter(event: KeyboardEvent) {
  if (!isImeEnter(event) && draft.value.trim()) commit()
}

const isGuess = computed(() => props.brain.contextSource === 'estimate')

function short(chars: number): string {
  return chars >= 1000 ? `${Math.round(chars / 1000)}k` : String(chars)
}
</script>

<template>
  <div class="row" :class="{ 'row--chosen': chosen }">
    <button
      class="pick"
      type="button"
      :aria-pressed="chosen"
      :aria-label="`用 ${brain.name}`"
      @click="emit('choose', brain.name)"
    >
      <span v-if="chosen" class="dot" />
    </button>

    <span class="name">{{ brain.name }}</span>
    <button
      class="model"
      :class="{ 'model--open': isPicking }"
      type="button"
      aria-haspopup="dialog"
      :aria-label="`换 ${brain.name} 的模型，现在是 ${brain.model}`"
      :title="brain.model"
      @click="emit('pick-model', brain.name)"
    >
      <span class="mono mname">{{ brain.model }}</span>
      <span class="caret" aria-hidden="true">▾</span>
    </button>
    <span
      class="budget"
      :class="{ 'budget--guess': isGuess }"
      :title="SOURCE_HINT[brain.contextSource]"
    >
      <span class="mono">{{ short(brain.contextChars) }}</span>
      <span class="src">{{ SOURCE_TAG[brain.contextSource] }}</span>
    </span>

    <span class="verdict" :class="{ 'verdict--tight': brain.fits === false || isGuess }">
      {{ brain.fitNote || brain.note }}
    </span>

    <span class="key">
      <template v-if="!brain.needsKey">
        <span class="mono local">本机 · 无需 key</span>
      </template>

      <template v-else-if="savedKey && !editing">
        <span class="mono saved" :title="brain.custom ? brain.baseUrl : undefined">
          {{ maskKey(savedKey) }}
        </span>
        <button class="link" type="button" @click="editing = true">换一个</button>
        <button class="link" type="button" @click="emit('forget', brain.name)">忘掉</button>
      </template>

      <template v-else>
        <input
          v-model="draft"
          class="mono input"
          type="password"
          autocomplete="off"
          spellcheck="false"
          placeholder="粘贴 key"
          @keydown.enter="commitOnEnter"
        />
        <button class="link" type="button" :disabled="!draft.trim()" @click="commit">保存</button>
        <button v-if="editing" class="link" type="button" @click="editing = false">取消</button>
      </template>
    </span>

    <button
      v-if="brain.custom"
      class="link drop"
      type="button"
      :aria-label="`删掉 ${brain.name}`"
      @click="emit('remove', brain)"
    >
      删掉
    </button>
    <span v-else class="drop-gap" />
  </div>
</template>

<style scoped>
.row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 14px;
  border-bottom: 1px solid var(--hair);
}

.row:last-child {
  border-bottom: none;
}

/* 朱批 = 你选的那个。选择是人做的动作，所以用这个颜色 */
.row--chosen {
  background: var(--mark-bg);
  border: 1px solid var(--mark);
}

.pick {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  background: none;
  border: 1.5px solid var(--edge-strong);
}

.row--chosen .pick {
  border-color: var(--mark);
}

.dot {
  width: 7px;
  height: 7px;
  background: var(--mark);
}

.name {
  width: 84px;
  flex-shrink: 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row--chosen .name {
  font-weight: 500;
}

/* 长得像一个下拉：虚线底 + 小三角。悬停时虚线变朱批——换模型是人的选择 */
.model {
  width: 142px;
  flex-shrink: 0;
  display: flex;
  align-items: baseline;
  gap: 4px;
  padding: 0;
  background: none;
  border: none;
  border-bottom: 1px dashed var(--edge-strong);
  text-align: left;
}

.model:hover,
.model:focus-visible,
.model--open {
  outline: none;
  border-bottom-color: var(--mark);
}

.mname {
  min-width: 0;
  flex-grow: 1;
  font-size: 11.5px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.caret {
  flex-shrink: 0;
  font-size: 9px;
  color: var(--text-muted);
}

.model:hover .caret,
.model--open .caret {
  color: var(--mark);
}

.budget {
  width: 64px;
  flex-shrink: 0;
  display: flex;
  justify-content: flex-end;
  align-items: baseline;
  gap: 5px;
  font-size: 11px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.src {
  font-size: 10px;
  color: var(--text-faint);
  white-space: nowrap;
}

/* 黄檗 = 等你判断：这个数是猜的 */
.budget--guess .src {
  color: var(--doubt);
}

.verdict {
  flex-grow: 1;
  min-width: 0;
  padding-left: 14px;
  font-size: 11.5px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 黄檗 = 等你判断。「装不下」不是错误，是一个要你决定怎么办的情况 */
.verdict--tight {
  color: var(--doubt);
}

.key {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 268px;
  flex-shrink: 0;
  justify-content: flex-end;
}

.input {
  width: 168px;
  height: 25px;
  padding: 0 8px;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
  font-size: 11px;
  color: var(--text);
}

.input:focus {
  outline: none;
  border-color: var(--mark);
}

.saved {
  font-size: 11px;
  color: var(--text-secondary);
  letter-spacing: 0.08em;
}

.local {
  font-size: 10.5px;
  color: var(--text-faint);
}

.link {
  padding: 0;
  background: none;
  border: none;
  font-size: 11.5px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.link:hover:not(:disabled) {
  color: var(--mark);
}

.link:disabled {
  color: var(--text-faint);
}

.drop {
  width: 32px;
  flex-shrink: 0;
  text-align: right;
}

.drop-gap {
  width: 32px;
  flex-shrink: 0;
}
</style>
