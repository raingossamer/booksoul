<script setup lang="ts">
/* 换模型（界面稿⑧屏）。点表格里的模型名弹出来，装在 `AppDialog` 里。
 *
 * 什么时候去问供应商：用户按下「列出模型」，或者刚保存了一个 key（view 替他按）。
 * 两种都是人按下去的那一下——不在展开时自动发：那一下要带着 key 去敲供应商的门。
 * 也不缓存：列表跟着 key 走，换一个 key 可能就是另一批。
 *
 * 排序服务端已经排好（对话类在前、明显不是对话的沉底），这里只做搜索。
 * 聚合平台一列几百个，没有搜索这个下拉就没法用。
 *
 * **手填永远在**（Grill D1）：模型名与窗口两个输入框，列不出来的服务照样能用。
 * 点列表里的一个 = 把它填进去；手填的名字不在刚拉到的列表里时提醒但不拦——
 * 列表本来就可能不全。窗口框预填存下来的那个数，改了就算「你填的」。
 * 按「用这个」才保存，而且只影响**以后开的会话**——这件事写在按钮旁边。
 *
 * 失败时：那句话用服务端给的（措辞只在 `brain_service._LIST_FAILURES` 一处），
 * **行为按 `code` 分支**，不匹配文案——超时、连不上是「再试一次」能解决的，
 * key 被拒、服务不给列表则不是，按钮不该怂恿用户去点一个没用的重试。
 *
 * 展示组件：不发请求、不读 store。拉列表、保存都是 view 的事，这里只 emit。
 */
import { computed, ref, watch } from 'vue'

import { SOURCE_HINT, SOURCE_TAG } from '@/lib/contextSource'
import { isImeEnter } from '@/lib/keys'
import type { Brain, ModelChoice, ModelEntry, ModelList } from '@/types'

export interface ListFailure {
  /** 服务端写死的 code；没有 code 的失败（如没填 key 的 400）是空串 */
  code: string
  message: string
}

const props = defineProps<{
  brain: Brain
  models: ModelList | null
  isLoading: boolean
  error: ListFailure | null
  isSaving: boolean
  saveError: string
}>()

const emit = defineEmits<{
  fetch: [name: string]
  save: [name: string, choice: ModelChoice]
  reset: [name: string]
}>()

const MAX_TOKENS = 10_000_000

const query = ref('')
// 要存的那一对：模型名、窗口（输入框里的原文，空 = 不知道），以及窗口的来路
const draftModel = ref('')
const draftTokens = ref('')
const draftSource = ref<ModelChoice['contextSource']>('')

// 展开时（以及保存后换了一行）从「现在在用的」起步
watch(
  () => props.brain,
  (brain) => {
    draftModel.value = brain.model
    draftTokens.value = brain.contextTokens ? String(brain.contextTokens) : ''
    draftSource.value = brain.contextTokens
      ? brain.contextSource === 'vendor'
        ? 'vendor'
        : 'manual'
      : ''
  },
  { immediate: true }
)

// 换了一批列表（重拉、或换了 key），旧的搜索词不再对得上
watch(
  () => props.models,
  () => {
    query.value = ''
  }
)

// 等一会儿再问一次有可能好的那两类
const TRANSIENT = new Set(['brain_timeout', 'brain_network'])
const isTransient = computed(() => TRANSIENT.has(props.error?.code ?? ''))

const fetchLabel = computed(() => {
  if (props.isLoading) return '正在问…'
  if (isTransient.value) return '再试一次'
  return props.models ? '重新列出' : '列出模型'
})

const shown = computed(() => {
  const items = props.models?.items ?? []
  const q = query.value.trim().toLowerCase()
  return q ? items.filter((m) => m.id.toLowerCase().includes(q)) : items
})

const model = computed(() => draftModel.value.trim())

/** 窗口框里的数；空是 0，填得不对是 null */
const tokens = computed<number | null>(() => {
  const raw = draftTokens.value.trim()
  if (!raw) return 0
  if (!/^\d+$/.test(raw)) return null
  const n = Number(raw)
  return n <= MAX_TOKENS ? n : null
})

// 手填的名字不在刚拉到的列表里：提醒，不拦。没拉过列表就无从说起
const isUnlisted = computed(
  () =>
    props.models !== null &&
    model.value !== '' &&
    !props.models.items.some((m) => m.id === model.value)
)

// 现在在用的这一个，窗口是猜的
const isGuessNow = computed(
  () =>
    props.brain.contextSource === 'estimate' && model.value === props.brain.model && !tokens.value
)

const isChanged = computed(
  () => model.value !== props.brain.model || tokens.value !== props.brain.contextTokens
)

const canSave = computed(
  () =>
    model.value !== '' &&
    model.value.length <= 64 &&
    tokens.value !== null &&
    isChanged.value &&
    !props.isSaving
)

function pick(entry: ModelEntry) {
  draftModel.value = entry.id
  // 供应商在列表里报了窗口就带上，并标「供应商」；没报就空着，交给内置表 / 估计
  draftTokens.value = entry.contextTokens ? String(entry.contextTokens) : ''
  draftSource.value = entry.contextTokens ? 'vendor' : ''
}

function typedModel() {
  // 供应商报的窗口属于刚才点的那一个；名字改了，那个数就不再是它的
  if (draftSource.value === 'vendor') {
    draftTokens.value = ''
    draftSource.value = ''
  }
}

function typedTokens() {
  draftSource.value = tokens.value ? 'manual' : ''
}

function save() {
  if (!canSave.value || tokens.value === null) return
  emit('save', props.brain.name, {
    model: model.value,
    contextTokens: tokens.value,
    contextSource: tokens.value ? draftSource.value || 'manual' : ''
  })
}

/** 两个输入框里回车就存；输入法上屏那一下回车不算（`lib/keys`） */
function saveOnEnter(event: KeyboardEvent) {
  if (!isImeEnter(event)) save()
}

function short(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)
}
</script>

<template>
  <div class="picker" role="group" :aria-label="`换 ${brain.name} 的模型`">
    <div class="bar">
      <span class="now">
        现在用 <span class="mono">{{ brain.model }}</span>
        <span v-if="brain.modelChosen" class="was">
          （预设是 <span class="mono">{{ brain.defaultModel }}</span
          >）
        </span>
      </span>
      <span class="grow" />
      <button
        v-if="brain.modelChosen && !brain.custom"
        class="link"
        type="button"
        :disabled="isSaving"
        @click="emit('reset', brain.name)"
      >
        恢复默认
      </button>
      <button class="fetch" type="button" :disabled="isLoading" @click="emit('fetch', brain.name)">
        {{ fetchLabel }}
      </button>
    </div>

    <p v-if="error" class="error" role="alert">{{ error.message }}</p>

    <template v-if="models">
      <p class="mono origin">共 {{ models.items.length }} 个，来自 {{ models.source }}</p>

      <input
        v-model="query"
        class="mono search"
        type="search"
        placeholder="搜模型名"
        autocomplete="off"
        spellcheck="false"
      />

      <ul class="list" role="listbox" :aria-label="`${brain.name} 的模型`">
        <li
          v-for="m in shown"
          :key="m.id"
          class="option"
          :class="{ 'option--picked': m.id === model }"
          role="option"
          :aria-selected="m.id === model"
          tabindex="0"
          @click="pick(m)"
          @keyup.enter="pick(m)"
          @dblclick="save"
        >
          <span class="mono oid">{{ m.id }}</span>
          <span v-if="m.id === brain.model" class="tag">正在用</span>
          <span v-if="m.contextTokens" class="mono win" title="供应商报的上下文窗口（token）">
            {{ short(m.contextTokens) }}
          </span>
        </li>
        <li v-if="!shown.length" class="empty">没有名字里带「{{ query }}」的模型</li>
      </ul>
    </template>

    <div class="fields">
      <label class="field field--model">
        <span class="label">模型</span>
        <input
          v-model="draftModel"
          class="mono input input--model"
          type="text"
          maxlength="64"
          autocomplete="off"
          spellcheck="false"
          placeholder="填模型名"
          @input="typedModel"
          @keydown.enter="saveOnEnter"
        />
      </label>
      <label class="field">
        <span class="label">窗口</span>
        <input
          v-model="draftTokens"
          class="mono input input--tokens"
          type="text"
          inputmode="numeric"
          autocomplete="off"
          placeholder="不知道就空着"
          @input="typedTokens"
          @keydown.enter="saveOnEnter"
        />
        <span class="unit">token</span>
      </label>
      <span v-if="draftSource" class="src" :title="SOURCE_HINT[draftSource]">
        {{ SOURCE_TAG[draftSource] }}
      </span>
    </div>

    <p v-if="tokens === null" class="warn">窗口要填 0 到 1000 万之间的整数。</p>
    <p v-else-if="isGuessNow" class="warn">现在的窗口是估计的，建议填上真实值。</p>
    <p v-if="isUnlisted" class="warn">列表里没有「{{ model }}」，核对一下拼写。</p>

    <p v-if="saveError" class="error" role="alert">{{ saveError }}</p>

    <div class="foot">
      <span class="after">只影响以后开的会话</span>
      <span class="grow" />
      <button class="use" type="button" :disabled="!canSave" @click="save">
        {{ isSaving ? '正在存…' : '用这个' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.picker {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 18px 18px;
}

.bar,
.fields,
.foot {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 弹窗窄的时候字段折到第二行，而不是把「窗口」挤出框外 */
.fields {
  flex-wrap: wrap;
  row-gap: 8px;
}

/* 后果和按钮放一处：按下去之前最后看到的那句话，就是它会影响什么 */
.foot {
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px solid var(--hair);
}

.now {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.now .mono {
  color: var(--text);
}

.was,
.now .was .mono {
  color: var(--text-muted);
}

.grow {
  flex-grow: 1;
}

/* 墨蓝 = 机器：这一下是去问供应商 */
.fetch {
  height: 25px;
  padding: 0 12px;
  background: var(--surface);
  border: 1px solid var(--machine);
  color: var(--machine);
  font-size: 11.5px;
}

.fetch:hover:not(:disabled) {
  background: var(--machine);
  color: var(--surface);
}

.fetch:disabled {
  border-color: var(--edge-strong);
  color: var(--text-faint);
}

/* 朱批实心 = 发起动作的主按钮：换模型是人的决定 */
.use {
  height: 25px;
  padding: 0 14px;
  background: var(--mark);
  border: 1px solid var(--mark);
  color: var(--surface);
  font-size: 11.5px;
}

.use:hover:not(:disabled) {
  background: var(--mark-strong);
  border-color: var(--mark-strong);
}

.use:disabled {
  background: var(--surface);
  border-color: var(--edge-strong);
  color: var(--text-faint);
}

.link {
  padding: 0;
  background: none;
  border: none;
  font-size: 11.5px;
  color: var(--text-secondary);
}

.link:hover:not(:disabled) {
  color: var(--mark);
}

.link:disabled {
  color: var(--text-faint);
}

.error,
.warn {
  margin: 0;
  font-size: 11.5px;
}

/* 黄檗 = 等你判断 */
.error,
.warn {
  color: var(--doubt);
}

.origin {
  margin: 0;
  font-size: 10.5px;
  color: var(--text-muted);
  overflow-wrap: anywhere;
}

.search,
.input {
  height: 25px;
  padding: 0 8px;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
  font-size: 11px;
  color: var(--text);
}

.search {
  width: 100%;
}

.field--model {
  flex: 1;
  min-width: 240px;
}

.input--model {
  flex: 1;
  min-width: 0;
}

.input--tokens {
  width: 96px;
  font-variant-numeric: tabular-nums;
}

.search:focus,
.input:focus {
  outline: none;
  border-color: var(--mark);
}

.field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.label,
.unit {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
}

.src {
  font-size: 10.5px;
  color: var(--text-muted);
  white-space: nowrap;
}

.list {
  max-height: 260px;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-bottom: 1px solid var(--hair);
  cursor: pointer;
}

.option:last-child {
  border-bottom: none;
}

.option:hover,
.option:focus-visible {
  outline: none;
  background: var(--paper);
}

/* 朱批 = 你选的那个 */
.option--picked {
  background: var(--mark-bg);
  box-shadow: inset 2px 0 0 var(--mark);
}

.oid {
  flex-grow: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag {
  font-size: 10px;
  color: var(--text-muted);
}

.win {
  font-size: 10.5px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
}

.empty {
  padding: 8px 10px;
  font-size: 11.5px;
  color: var(--text-faint);
}

.after {
  font-size: 10.5px;
  color: var(--text-muted);
  white-space: nowrap;
}
</style>
