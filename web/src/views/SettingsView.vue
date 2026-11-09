<script setup lang="ts">
/* 设置 · 大脑与人格（界面稿⑧屏）。
 *
 * 一屏两半，回答的是同一个问题的两面：**接哪个模型**（左），
 * **用什么口气读**（右）。左半还管第三件事：**你的 key 放在哪**。
 *
 * 第二件事上这个产品的立场是明确的，所以页面上那段话写的是事实，不是安慰：
 * key 存在这台浏览器的 localStorage，提问时经服务器转发给模型厂商，
 * 服务器只是转发 —— 不保存、不记录。服务端那一侧有测试钉着（日志白名单、
 * 异常处理器不回显请求头），不是一句承诺。
 *
 * 带上 `?bookId=` 就多一列「这本书装得进它吗」，判断用的是选章时同一套额度。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getBook } from '@/api/books'
import { addBrain, deleteBrain, listBrains, listModels, resetModel, saveModel } from '@/api/brains'
import { ApiError } from '@/api/client'
import AddEndpoint from '@/components/brain/AddEndpoint.vue'
import BrainRow from '@/components/brain/BrainRow.vue'
import ModelPicker, { type ListFailure } from '@/components/brain/ModelPicker.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import AppShell from '@/components/layout/AppShell.vue'
import PersonaEditor from '@/components/persona/PersonaEditor.vue'
import {
  brainForNewChat,
  forgetBrainKey,
  isReady,
  readBrainKey,
  readChosenBrain,
  saveBrainKey,
  saveChosenBrain
} from '@/lib/brainKeys'
import type { Brain, ModelChoice, ModelList } from '@/types'

const route = useRoute()
const router = useRouter()

const brains = ref<Brain[]>([])
const keys = ref<Record<string, string>>({})
const chosen = ref('')
const error = ref('')
const adding = ref(false)
const addError = ref('')
const busy = ref(false)
const bookLabel = ref('')

// 换模型：同一时刻只展开一行。列表不缓存——跟着 key 走，换一个 key 可能就是另一批
const picking = ref<string | null>(null)
const models = ref<ModelList | null>(null)
const isListing = ref(false)
const listError = ref<ListFailure | null>(null)
const isSavingModel = ref(false)
const saveModelError = ref('')
// 从书籍详情进来、选的大脑还缺 key：记着它，填好 key 就回书籍详情
const returnAfterKey = ref<string | null>(null)

const bookId = computed(() => {
  const raw = route.query.bookId
  const n = Number(typeof raw === 'string' ? raw : '')
  return Number.isFinite(n) && n > 0 ? n : undefined
})

function refreshKeys() {
  const next: Record<string, string> = {}
  for (const brain of brains.value) next[brain.name] = readBrainKey(brain.name)
  keys.value = next
}

async function load() {
  try {
    brains.value = await listBrains(bookId.value)
    refreshKeys()
    // 圆点点在「新开会话真正会用的那一个」上：和书籍详情、开会话读同一条规则。
    // 原先这里自己兜底成列表第一个，开会话那边兜底成 claude——两处各点一个
    chosen.value = brainForNewChat(brains.value.map((b) => b.name))
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '大脑列表读不出来。'
  }

  if (bookId.value) {
    try {
      const book = await getBook(bookId.value)
      bookLabel.value = `「这本书」= ${book.title} · ${book.wordCount.toLocaleString()} 字`
    } catch {
      // 书读不出来不该拖垮整个设置页：上面那张表本来就不依赖它
      bookLabel.value = ''
    }
  }
}

function choose(name: string) {
  chosen.value = name
  saveChosenBrain(name)
  // 从书籍详情「选大脑 →」进来的：人是要回去开会话的。能用就回去；
  // 还缺 key 就留下先填——回去了也只会在「开始对话」那一下被拦住。
  if (!bookId.value) return
  if (isUsable(name)) backToBook()
  else returnAfterKey.value = name
}

function isUsable(name: string): boolean {
  const brain = brains.value.find((b) => b.name === name)
  return brain !== undefined && isReady(brain)
}

function backToBook() {
  returnAfterKey.value = null
  void router.push(`/books/${bookId.value}`)
}

function save(name: string, key: string) {
  saveBrainKey(name, key)
  keys.value = { ...keys.value, [name]: readBrainKey(name) }
  if (!keys.value[name]) return
  // 选了它、只差 key 的那一位：key 填好了，回书籍详情去开会话。想换模型再从这里进
  if (bookId.value && returnAfterKey.value === name) {
    backToBook()
    return
  }
  // 刚填好（或换了）key：当场把这家的模型列出来，让你挑一个当默认模型。
  // 保存 key 就是人按下去的那一下；换了 key 能用的模型也可能跟着变。
  openPicker(name, { fetch: true })
}

function forget(name: string) {
  forgetBrainKey(name)
  keys.value = { ...keys.value, [name]: '' }
}

function openPicker(name: string, { fetch = false } = {}) {
  picking.value = name
  models.value = null
  listError.value = null
  saveModelError.value = ''
  if (fetch) void fetchModels(name)
}

// 弹窗里那一行。保存后整张表会重拉，这里跟着拿到新的那一份
const pickingBrain = computed(() => brains.value.find((b) => b.name === picking.value) ?? null)

function closePicker() {
  picking.value = null
  models.value = null
}

async function chooseModel(name: string, choice: ModelChoice) {
  isSavingModel.value = true
  saveModelError.value = ''
  try {
    await saveModel(name, choice)
    closePicker()
    // 重拉整张表：额度、来源、「这本书」那一格都跟着新模型变
    await load()
  } catch (e) {
    saveModelError.value = e instanceof ApiError ? e.message : '没能存下来，再试一次。'
  } finally {
    isSavingModel.value = false
  }
}

async function restoreModel(name: string) {
  isSavingModel.value = true
  saveModelError.value = ''
  try {
    await resetModel(name)
    closePicker()
    await load()
  } catch (e) {
    saveModelError.value = e instanceof ApiError ? e.message : '没能恢复默认，再试一次。'
  } finally {
    isSavingModel.value = false
  }
}

async function fetchModels(name: string) {
  isListing.value = true
  listError.value = null
  try {
    const found = await listModels(name)
    // 等回来的时候用户可能已经换到别的行了：迟到的列表不能填进另一行的下拉
    if (picking.value === name) models.value = found
  } catch (e) {
    if (picking.value === name) {
      listError.value =
        e instanceof ApiError
          ? { code: e.code, message: e.message }
          : { code: '', message: '模型列表没拿到。' }
    }
  } finally {
    isListing.value = false
  }
}

async function create(payload: {
  name: string
  model: string
  baseUrl: string
  contextTokens: number
}) {
  busy.value = true
  addError.value = ''
  try {
    await addBrain(payload)
    adding.value = false
    await load()
  } catch (e) {
    addError.value = e instanceof ApiError ? e.message : '这个端点没能加进来。'
  } finally {
    busy.value = false
  }
}

async function remove(brain: Brain) {
  if (brain.id === null) return
  // 删掉的不只是这一行：它的 key 一起忘掉，用它开的会话也接不下去了（问的时候
  // 服务端找不到这个大脑）。每次都有东西会丢，所以每次都问
  if (
    !window.confirm(
      `删掉「${brain.name}」？这台浏览器里存的 key 一起删掉，用它开的会话之后不能再问。`
    )
  ) {
    return
  }
  try {
    await deleteBrain(brain.id)
    // key 跟着端点一起忘掉：端点没了还留着 key，那串东西就再也没人管了
    forgetBrainKey(brain.name)
    // 选的正是它：把这个选择也忘掉，新开的会话回到默认，而不是去找一个不存在的大脑
    if (readChosenBrain() === brain.name) saveChosenBrain('')
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '这个端点没能删掉。'
  }
}

onMounted(load)
</script>

<template>
  <AppShell title="大脑与人格" hint="接哪个模型读，用什么口气读">
    <template #head-right>
      <span v-if="bookLabel" class="mono book">{{ bookLabel }}</span>
    </template>

    <p v-if="error" class="banner" role="alert">{{ error }}</p>
    <p v-else-if="returnAfterKey" class="banner" role="status">
      「{{ returnAfterKey }}」还缺 key，填好保存后回书籍详情。
    </p>

    <div class="split">
      <div class="body">
        <div class="head-row">
          <span class="mono cap cap--pick" />
          <span class="mono cap cap--name">预设</span>
          <span class="mono cap cap--model">模型</span>
          <span class="mono cap cap--budget">正文额度</span>
          <span class="mono cap cap--verdict">{{ bookId ? '这本书' : '说明' }}</span>
          <span class="mono cap cap--key">你的 key</span>
          <span class="cap--drop" />
        </div>

        <div class="table">
          <BrainRow
            v-for="brain in brains"
            :key="brain.name"
            :brain="brain"
            :chosen="brain.name === chosen"
            :saved-key="keys[brain.name] ?? ''"
            :is-picking="picking === brain.name"
            @choose="choose"
            @save="save"
            @forget="forget"
            @remove="remove"
            @pick-model="openPicker"
          />
        </div>

        <AddEndpoint
          v-if="adding"
          :busy="busy"
          :error="addError"
          @submit="create"
          @cancel="adding = false"
        />
        <button v-else class="add-row" type="button" @click="adding = true">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">
            <path d="M12 5v14" />
            <path d="M5 12h14" />
          </svg>
          <span>添加自定义端点（OpenAI 兼容）</span>
        </button>

        <div class="grow" />

        <section class="rule">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round">
            <rect x="4" y="10" width="16" height="10" />
            <path d="M8 10V7a4 4 0 0 1 8 0v3" />
          </svg>
          <div class="rule-text">
            <p><b>key 只存在这台浏览器</b>，服务器只转发、不保存。</p>
            <p class="rule-sub">按 token 计费，钱走你自己的 key；换浏览器要重填。</p>
          </div>
        </section>
      </div>

      <PersonaEditor />
    </div>

    <AppDialog v-if="pickingBrain" :title="`换模型 · ${pickingBrain.name}`" @close="closePicker">
      <ModelPicker
        :brain="pickingBrain"
        :models="models"
        :is-loading="isListing"
        :error="listError"
        :is-saving="isSavingModel"
        :save-error="saveModelError"
        @fetch="fetchModels"
        @save="chooseModel"
        @reset="restoreModel"
      />
    </AppDialog>
  </AppShell>
</template>

<style scoped>
.book {
  font-size: 10.5px;
  color: var(--text-faint);
}

.banner {
  margin: 20px 30px 0;
  padding: 12px 16px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
}

.split {
  flex-grow: 1;
  display: flex;
  align-items: stretch;
  min-height: 0;
}

.body {
  flex-grow: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 30px 0;
  min-height: 0;
  overflow-y: auto;
}

.head-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 14px 2px;
}

.cap {
  font-size: 9.5px;
  letter-spacing: 0.12em;
  color: var(--text-faint);
}

.cap--pick {
  width: 15px;
  flex-shrink: 0;
}

.cap--name {
  width: 84px;
  flex-shrink: 0;
}

.cap--model {
  width: 142px;
  flex-shrink: 0;
}

.cap--budget {
  width: 64px;
  flex-shrink: 0;
  text-align: right;
}

.cap--verdict {
  flex-grow: 1;
  padding-left: 14px;
}

.cap--key {
  width: 268px;
  flex-shrink: 0;
  text-align: right;
}

.cap--drop {
  width: 32px;
  flex-shrink: 0;
}

.table {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.add-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: none;
  border: 1px dashed var(--dash);
  color: var(--text-muted);
  font-size: 13px;
}

.add-row:hover {
  border-color: var(--mark);
  color: var(--mark);
}

.add-row svg {
  width: 15px;
  height: 15px;
  stroke: currentColor;
}

.grow {
  flex-grow: 1;
  min-height: 12px;
}

.rule {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 13px 15px;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.rule svg {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  margin-top: 3px;
  stroke: var(--text-secondary);
}

.rule-text {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.rule-text p {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.85;
  color: var(--text-secondary);
}

.rule-text b {
  font-weight: 500;
  color: var(--text);
}

.rule-sub {
  color: var(--text-muted) !important;
}
</style>
