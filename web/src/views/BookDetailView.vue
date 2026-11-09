<script setup lang="ts">
/* 书籍详情（界面稿④屏）。
 *
 * 左边是这本书的来历（元信息 + 装得进哪个大脑），右边是目录树。
 * 元信息为空就写「未标注」—— 不替来源猜。译者尤其不能猜：人格提示词里会出现
 * 「译者」，猜错了它会一路错下去。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getBook, getBookState, reparseBook, resolveNote } from '@/api/books'
import { ApiError } from '@/api/client'
import ChapterTree from '@/components/book/ChapterTree.vue'
import ConversationList from '@/components/chat/ConversationList.vue'
import NoteBanner from '@/components/common/NoteBanner.vue'
import AppShell from '@/components/layout/AppShell.vue'
import BackButton from '@/components/common/BackButton.vue'
import { useConversations } from '@/composables/useConversations'
import { brainForNewChat } from '@/lib/brainKeys'
import type { BookDetail, Chapter, Note } from '@/types'

/** 还在解析时隔多久回来看一眼（与书库那条轮询同一个节奏）。 */
const POLL_MS = 1500

const route = useRoute()
const router = useRouter()

const book = ref<BookDetail | null>(null)
const error = ref('')
const loading = ref(true)
let timer: ReturnType<typeof setTimeout> | null = null

const bookId = computed(() => Number(route.params.id))
/** 解析好了才能问、才能读。解析中的书点「开始对话」只会在第一问那一下被服务端拦住 */
const ready = computed(() => book.value?.status === 'ready')

// 这本书的会话：「开始对话」每点一次是一个新的，接着上次聊得从这里进
const {
  items: history,
  removing,
  error: removeError,
  load: loadHistory,
  remove: removeHistory
} = useConversations(20, () => bookId.value)

// 「开始对话」会用的那个大脑。从⑧屏选完回来，这张表上要看得出选的是谁；
// 选的那个已经不在了（自定义端点被删掉），标出来的是开会话真正会用的那一个
const chosenBrain = computed(() => brainForNewChat(book.value?.brains.map((b) => b.name)))

const meta = computed(() => {
  const b = book.value
  if (!b) return []
  const rows: { label: string; value: string; annotated: boolean }[] = [
    { label: '作者', value: b.author, annotated: Boolean(b.author) },
    { label: '译者', value: b.translator, annotated: Boolean(b.translator) },
    { label: '贡献者', value: b.contributor, annotated: Boolean(b.contributor) },
    { label: '出版社', value: b.publisher, annotated: Boolean(b.publisher) },
    { label: '出版日期', value: b.published, annotated: Boolean(b.published) },
    { label: 'ISBN', value: b.isbn, annotated: Boolean(b.isbn) },
    { label: '语言', value: b.language, annotated: Boolean(b.language) }
  ]
  return rows.map((r) => ({ ...r, value: r.annotated ? r.value : '未标注' }))
})

// 第一次解析还没出结果时，元信息一项都还没读出来——那时写「未标注」是替来源下结论
const metaKnown = computed(
  () => book.value?.status !== 'parsing' || meta.value.some((row) => row.annotated)
)

const shape = computed(() => {
  const b = book.value
  if (!b || !b.chapters.length) return ''
  const counts = b.chapters.map((c) => c.wordCount)
  return `${b.chapterCount} 节 · ${b.wordCount.toLocaleString()} 字 · 最长 ${Math.max(
    ...counts
  ).toLocaleString()} · 最短 ${Math.min(...counts).toLocaleString()}`
})

const unresolved = computed(() => book.value?.notes.filter((n) => !n.resolved) ?? [])

// 「打开阅读」落在第一节，不是第 1 个 idx 写死 —— 重解析后序号未必从 1 起
const firstIdx = computed(() => book.value?.chapters[0]?.idx ?? 0)

function openChapter(chapter: Chapter) {
  void router.push(`/books/${bookId.value}/read/${chapter.idx}`)
}

async function load() {
  loading.value = true
  // 换到另一本书：上一本的内容不能带着新一本的错误一起摆着
  if (book.value?.id !== bookId.value) book.value = null
  error.value = ''
  void loadHistory()
  await refreshBook()
}

async function refreshBook() {
  const asked = bookId.value
  try {
    const got = await getBook(asked)
    // 回来时已经换到另一本书了：这一份不属于眼前这一页
    if (asked !== bookId.value) return
    book.value = got
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '这本书读不出来。'
  } finally {
    loading.value = false
    schedule()
  }
}

/**
 * 还在解析：隔一会儿问一句「好了没有」，好了再拉一次整份详情，这一页自己变过来。
 * 判据是库里的状态，刷新页面照样接得上。
 *
 * 每拍只问状态（一条查询），不拉整份详情：详情要把装得进哪个大脑、审核记录、会话数都算一遍，
 * 一秒半一次，解析一本大部头的那几十秒里全是白算。
 */
async function watchParse() {
  const asked = bookId.value
  try {
    const state = await getBookState(asked)
    // 回来时已经换到另一本书了：这一拍不属于眼前这一页
    if (asked !== bookId.value) return
    if (state.status !== 'parsing') {
      // 有结局了：章节、字数、存疑都随解析结果变了，拉一次整份的
      await refreshBook()
      return
    }
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '这本书读不出来。'
  }
  schedule()
}

function schedule() {
  if (timer) clearTimeout(timer)
  timer = null
  if (book.value?.status === 'parsing') timer = setTimeout(() => void watchParse(), POLL_MS)
}

async function markResolved(note: Note) {
  try {
    await resolveNote(bookId.value, note.id)
    note.resolved = true
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '标记没能保存。'
  }
}

/**
 * 重新解析。
 *
 * **有会话在用这本书时先问一句。** 重解析会把 chapters 整张重建，那几个会话
 * 锁定的章节 id 随之作废、被清空，下一轮要重挑一次、整本书的缓存跟着失效，
 * 输入按全价重算。旧引文会被重新定位，找不到的降级成「原位置已变更」。
 * 这些都不是错误，但都是用户的钱和他攒下来的东西，不该在他不知情时发生。
 *
 * 没有会话就不问：那时重解析只是重跑一遍，没有任何东西会丢。
 */
async function reparse() {
  const count = book.value?.conversationCount ?? 0
  if (
    count > 0 &&
    !window.confirm(
      `这本书有 ${count} 个会话。重新解析后，它们的下一轮按全价计费，` +
        '部分引文可能找不回原位置。继续吗？'
    )
  ) {
    return
  }
  try {
    await reparseBook(bookId.value)
    // 留在这一页：它变成「解析中」，隔一会儿自己看一眼，好了就变回来。
    // 原先一点就被送回书库，要再点进来才看得到结果
    await refreshBook()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '重新解析没能开始。'
  }
}

onMounted(load)
watch(bookId, load)
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <AppShell :title="book?.title || '书籍'" :hint="book?.sourceFormat.toUpperCase()">
    <template #head-right>
      <BackButton to="/" label="回书库" />
    </template>

    <!-- 出错是一条横幅，不把已经摆出来的书收走：轮询时网络抖一下、标记没存上，
         都不该让整页换成一句话 -->
    <p v-if="error" class="banner" role="alert">{{ error }}</p>

    <!-- 靛 = 机器在做事；重墨 = 停下来了。就绪不给颜色，这一条整条不出现 -->
    <p v-if="book?.status === 'parsing'" class="state state--parsing" role="status">
      <span class="state-bar" aria-hidden="true"><span class="state-fill" /></span>
      正在解析，好了这一页会自己更新。
    </p>
    <p v-else-if="book?.status === 'failed'" class="state state--failed" role="alert">
      这一次解析停下了。
      <button class="state-act" type="button" @click="reparse">重新解析</button>
    </p>

    <div v-if="book" class="split">
      <aside class="side">
        <section v-if="metaKnown" class="block">
          <span class="mono cap">这本书</span>
          <dl class="meta">
            <template v-for="row in meta" :key="row.label">
              <dt>{{ row.label }}</dt>
              <dd :class="{ 'dd--blank': !row.annotated }">{{ row.value }}</dd>
            </template>
          </dl>
        </section>

        <section class="block">
          <div class="cap-line">
            <span class="mono cap">装得进哪个大脑</span>
            <span class="grow" />
            <RouterLink class="mono pick" :to="`/settings?bookId=${book.id}`">选大脑 →</RouterLink>
          </div>
          <div class="brains">
            <div
              v-for="b in book.brains"
              :key="b.name"
              class="brain"
              :class="{ 'brain--chosen': b.name === chosenBrain }"
            >
              <span class="mono bname" :title="b.name === chosenBrain ? '开始对话会用它' : ''">
                {{ b.name }}
              </span>
              <span class="mono bctx">{{ Math.round(b.contextChars / 1000) }}k</span>
              <!-- 与⑧屏同一份结论：窗口是估计的就不说装不装得下。这一栏窄，
                   「按估计」挪进悬停说明，一行放得下（实测两行会把表撑乱） -->
              <span
                class="bfit"
                :class="{ 'bfit--no': b.fits !== true }"
                :title="b.fits === null ? '窗口是估计的，可以在设置里填上真实值' : ''"
              >
                {{ b.fits === null ? '窗口未知' : b.fits ? '整本装得下' : '要挑章节' }}
              </span>
            </div>
          </div>
        </section>

        <div class="grow" />

        <section v-if="history.length" class="block">
          <span class="mono cap">这本书的会话</span>
          <p v-if="removeError" class="side-error" role="alert">{{ removeError }}</p>
          <div class="history">
            <ConversationList
              :items="history"
              :removing="removing"
              stacked
              @remove="removeHistory"
            />
          </div>
        </section>

        <div class="actions">
          <RouterLink v-if="ready" class="act act--lead" :to="`/books/${book.id}/chat`">
            开始对话
          </RouterLink>
          <span v-else class="act act--off" aria-disabled="true">开始对话</span>
          <RouterLink v-if="firstIdx" class="act" :to="`/books/${book.id}/read/${firstIdx}`">
            打开阅读
          </RouterLink>
          <RouterLink class="act" :to="`/books/${book.id}/report`">解析报告</RouterLink>
          <button class="act" type="button" :disabled="book.status === 'parsing'" @click="reparse">
            重新解析
          </button>
        </div>
      </aside>

      <section class="main">
        <header class="main-head">
          <h2>目录结构</h2>
          <span class="mono shape">{{ shape }}</span>
          <span class="grow" />
          <span class="aside">标黄的节偏短，点开核对</span>
        </header>

        <div class="tree-wrap">
          <ChapterTree :chapters="book.chapters" @open="openChapter" />
          <div class="grow" />
          <!-- 内容审核拦过（S13）。重墨 = 停下来了：这条路走不通，
               不是「你看一眼」。没被拦过就整条不出现 —— 成功不给颜色。 -->
          <div v-if="book.moderation" class="blocked">
            <span class="mono blocked-tag">停下了</span>
            <div class="blocked-text">
              <p>{{ book.moderation.advice }}</p>
              <p class="blocked-sub">
                已经被拦
                <span class="mono">{{ book.moderation.times }}</span>
                次 · 最近一次是
                <span class="mono">{{ book.moderation.brainName }}</span>
                <template v-if="!book.moderation.pinned">
                  —— 嫌疑还在
                  <span class="mono">{{ book.moderation.suspectTitles.length }}</span>
                  节之间
                </template>
              </p>
            </div>
            <RouterLink class="blocked-act" to="/settings">换个大脑</RouterLink>
          </div>

          <div v-if="unresolved.length" class="notes">
            <NoteBanner
              v-for="note in unresolved"
              :key="note.id"
              :note="note"
              @resolve="markResolved"
            />
          </div>
        </div>
      </section>
    </div>

    <p v-if="!book && !error && !loading" class="banner">没有这本书。</p>
  </AppShell>
</template>

<style scoped>
/* 重墨 = 停下来了。整块墨底而不是第二种红：红在这套视觉里归「人做的标记」。
   和 NoteBanner 的 banner--block 同一套样式语言，但它不是一条 parse_note
   （审核是运行时的事，不是解析发现的），所以不复用那个组件。 */
.blocked {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 12px 14px;
  background: var(--ink);
  color: var(--ink-text);
}

.blocked-tag {
  flex-shrink: 0;
  padding: 2px 6px;
  font-size: 10px;
  color: var(--ink);
  background: var(--doubt-on-dark);
}

.blocked-text {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.blocked-text p {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.75;
}

.blocked-sub {
  color: var(--doubt-on-dark);
}

.blocked-act {
  flex-shrink: 0;
  padding: 5px 11px;
  font-size: 11.5px;
  color: var(--ink);
  background: var(--doubt-on-dark);
  text-decoration: none;
}

.banner {
  margin: 30px 40px;
  padding: 12px 16px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
}

.split {
  flex-grow: 1;
  display: flex;
  min-height: 0;
}

.side {
  width: 340px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 22px 26px 20px;
  border-right: 1px solid var(--rule);
  background: var(--panel);
  overflow-y: auto;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

/* 会话多了自己滚，别把底下「开始对话」那排按钮挤出屏幕 */
.history {
  max-height: 232px;
  overflow-y: auto;
}

/* 删不掉（多半是那个会话还在回答）。黄檗 = 等一等再说，不是坏了 */
.side-error {
  margin: 0;
  padding: 7px 10px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--doubt);
}

.cap-line {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.cap-line .grow {
  flex-grow: 1;
}

.cap {
  font-size: 10px;
  letter-spacing: 0.16em;
  color: var(--text-faint);
}

.pick {
  font-size: 10.5px;
  color: var(--text-secondary);
  text-decoration: none;
}

.pick:hover {
  color: var(--mark);
}

.meta {
  display: grid;
  grid-template-columns: 64px 1fr;
  gap: 5px 10px;
  margin: 0;
  font-size: 12.5px;
}

.meta dt {
  color: var(--text-muted);
}

.meta dd {
  margin: 0;
  color: var(--text);
  word-break: break-word;
}

/* 「未标注」是一句实话，不是缺省值，所以它看起来要和有值的不一样 */
.dd--blank {
  color: var(--text-faint);
  font-style: italic;
}

.brains {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.brain {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--hair);
}

/* 朱批 = 你选的那个（与⑧屏同一种标法）。「开始对话」会用它 */
.brain--chosen {
  background: var(--mark-bg);
  box-shadow: inset 2px 0 0 var(--mark);
}

.brain--chosen .bname {
  color: var(--mark);
  font-weight: 500;
}

.brain:last-child {
  border-bottom: none;
}

.bname {
  width: 92px;
  flex-shrink: 0;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bctx {
  width: 44px;
  flex-shrink: 0;
  text-align: right;
  font-size: 10.5px;
  color: var(--text-faint);
}

.bfit {
  flex-grow: 1;
  text-align: right;
  font-size: 11.5px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.bfit--no {
  color: var(--doubt);
}

.grow {
  flex-grow: 1;
}

.actions {
  display: flex;
  gap: 7px;
}

.act {
  flex-grow: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 33px;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
  color: var(--text-secondary);
  font-size: 12.5px;
  text-decoration: none;
}

.act:hover {
  border-color: var(--mark);
  color: var(--mark);
}

/* 主按钮用朱批描边。这一屏上「读它」才是正事，其余是核对用的 */
.act--lead {
  border-color: var(--mark);
  color: var(--mark);
}

/* 还不能点的（书没解析好）：灰掉，悬停也不着朱批 */
.act--off,
.act--off:hover,
.act:disabled:hover {
  border-color: var(--edge-strong);
  color: var(--text-faint);
  cursor: not-allowed;
}

.state {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0;
  padding: 10px 40px;
  border-bottom: 1px solid var(--rule);
  font-size: 12px;
}

.state--parsing {
  background: var(--surface);
  color: var(--machine);
}

.state-bar {
  width: 72px;
  height: 3px;
  flex-shrink: 0;
  background: var(--hair);
  overflow: hidden;
}

/* 进度未知：一段来回走的条，与书库那张「解析中」卡片同一个样子 */
.state-fill {
  display: block;
  width: 38%;
  height: 100%;
  background: var(--machine);
  animation: slide 1.6s ease-in-out infinite;
}

@keyframes slide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(263%);
  }
}

.state--failed {
  background: var(--ink);
  color: var(--ink-text);
}

.state-act {
  padding: 3px 11px;
  background: var(--doubt-on-dark);
  border: none;
  color: var(--ink);
  font-size: 11.5px;
}

.main {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.main-head {
  display: flex;
  align-items: baseline;
  gap: 13px;
  padding: 24px 30px 14px;
  border-bottom: 1px solid var(--rule);
}

.main-head h2 {
  font-size: 17px;
}

.shape {
  font-size: 11px;
  color: var(--text-muted);
}

.aside {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.tree-wrap {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  padding: 12px 30px 18px;
  overflow-y: auto;
}

.notes {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 14px;
}
</style>
