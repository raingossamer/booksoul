<script setup lang="ts">
/* 阅读器（界面稿⑤屏）。
 *
 * 这一屏的承诺写在它自己底下那行路径上：**屏幕上这些字，和你照着那条路径
 * 拿到的文件，是同一份东西**。所以「看 Markdown 源」不是个开发者彩蛋，
 * 它是这句承诺的验证方式——一键就能对一遍。
 *
 * 目录树、位置条、上一节下一节都只是把书翻开的方式，不改变正文一个字。
 *
 * 带 `?cite=` 进来就是从对话跳过来的：顶上那条朱批色的横条说清楚是**哪个问题**
 * 把人带到这里的，被引的那一段在正文里标出来、并且直接滚到它那里，右边给一条回去的路
 * （回到点「去原文」的那条回答上，不是对话的底部）。没有这条横条，用户点完「去原文」
 * 只会落在一页陌生的正文中间，不知道自己是怎么来的。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getBook, getBookImage, getChapter } from '@/api/books'
import { getCitation } from '@/api/citations'
import { ApiError } from '@/api/client'
import ChapterProse from '@/components/book/ChapterProse.vue'
import ReaderToc from '@/components/book/ReaderToc.vue'
import IconRail from '@/components/layout/IconRail.vue'
import { locateBlock, paragraphOrdinal } from '@/lib/citations'
import { renderChapter } from '@/lib/markdown'
import type { BookDetail, ChapterContent, Citation } from '@/types'

const route = useRoute()
const router = useRouter()

const book = ref<BookDetail | null>(null)
const chapter = ref<ChapterContent | null>(null)
const error = ref('')
const showSource = ref(false)
const scroller = ref<HTMLElement | null>(null)

const cited = ref<Citation | null>(null)

const bookId = computed(() => Number(route.params.id))
const idx = computed(() => Number(route.params.idx))

const prose = computed(() => (chapter.value ? renderChapter(chapter.value.markdown) : null))

/**
 * 这一节引的图：文件名 → blob: 地址。
 *
 * `<img src>` 带不上 token，所以带着 token 取成 blob 再给它一个本地地址。那些地址
 * 占着内存，换一节、离开这一页都要还回去。取不到的那张就不显示，正文照读。
 */
const images = ref(new Map<string, string>())

function releaseImages() {
  images.value.forEach((url) => URL.revokeObjectURL(url))
  images.value = new Map()
}

watch(prose, (rendered) => {
  releaseImages()
  if (!rendered) return
  const book = bookId.value
  for (const name of rendered.images) {
    getBookImage(book, name)
      .then((blob) => {
        // 等回来时已经翻到别的节了：这张图不属于眼前这一页
        if (prose.value !== rendered) return
        images.value = new Map(images.value).set(name, URL.createObjectURL(blob))
      })
      .catch(() => {})
  }
})

onBeforeUnmount(releaseImages)

/**
 * 被引的那一块。-1 = 没找着，那就不画高亮。
 *
 * 服务端给的段号只是快路：那个数是另一段代码按 Markdown 源数出来的，
 * 两边一旦分叉就会把高亮画到隔壁那一段上——而那一段看起来同样像是出处。
 * 所以以原文为准绳，`locateBlock` 会先核对再决定。
 */
const highlight = computed(() => {
  if (!prose.value || !cited.value?.located) return -1
  return locateBlock(prose.value.blocks, cited.value)
})

/** 给人看的段序数：只数段落，标题和那行元信息不算。 */
const citedOrdinal = computed(() =>
  prose.value && highlight.value >= 0 ? paragraphOrdinal(prose.value.blocks, highlight.value) : 0
)

function titleOf(at: number | null): string {
  if (at === null) return ''
  const found = book.value?.chapters.find((c) => c.idx === at)
  return found?.title || `第 ${at} 节`
}

async function citationFor(raw: unknown): Promise<Citation | null> {
  const id = Number(raw)
  if (!id) return null
  try {
    return await getCitation(id)
  } catch {
    // 引文取不到不该把整页读书的体验毁掉：正文照读，只是没有那条来路条
    return null
  }
}

async function load() {
  error.value = ''
  showSource.value = false
  try {
    // 目录只在换书时重取：翻一节不该把整本书的目录再拉一遍
    if (book.value?.id !== bookId.value) book.value = await getBook(bookId.value)
    // 正文与引文一起取、一起换上：先换正文再等引文，中间那一下新的一节会停在
    // 上一节的滚动位置上，等引文回来再跳一次
    const [loaded, citation] = await Promise.all([
      getChapter(bookId.value, idx.value),
      citationFor(route.query.cite)
    ])
    chapter.value = loaded
    cited.value = citation
    await nextTick()
    reveal()
  } catch (e) {
    chapter.value = null
    error.value = e instanceof ApiError ? e.message : '这一节读不出来。'
  }
}

/**
 * 换上一节之后停在哪：从对话跳来的，落在被引的那一段上（原先只标出来、不滚过去，
 * 那一段在第二十段的话，得自己往下找）；否则从头开始看，不停在上一节的滚动位置上。
 */
function reveal() {
  const el = scroller.value
  if (!el) return
  const at = highlight.value
  const block = at >= 0 ? el.querySelector<HTMLElement>(`[data-block="${at}"]`) : null
  if (block) block.scrollIntoView({ block: 'center' })
  else el.scrollTo({ top: 0 })
}

function go(at: number | null) {
  if (at !== null) void router.push(`/books/${bookId.value}/read/${at}`)
}

/** ← / → 翻节。在输入框里、带着修饰键、别处已经处理过的，都不管 */
function onKey(event: KeyboardEvent) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
  if (event.shiftKey || !chapter.value) return
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, select, [contenteditable]')) return
  if (event.key === 'ArrowLeft') go(chapter.value.prevIdx)
  else if (event.key === 'ArrowRight') go(chapter.value.nextIdx)
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

watch([bookId, idx, () => route.query.cite], load, { immediate: true })
</script>

<template>
  <div class="shell">
    <IconRail />

    <ReaderToc
      v-if="book"
      :book-id="book.id"
      :title="book.title"
      :chapters="book.chapters"
      :word-count="book.wordCount"
      :current-idx="idx"
      :preceding-words="chapter?.precedingWords ?? 0"
    />

    <main class="main">
      <!-- 从对话跳来。朱批 = 人的标记：是读者提的那个问题把这一段挑出来的 -->
      <div v-if="cited" class="trail">
        <svg
          class="trail-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M9 6L3 12l6 6" />
          <path d="M3 12h12a6 6 0 0 1 6 6v1" />
        </svg>
        <span class="mono trail-cap">从对话跳来</span>
        <span class="trail-text">
          「{{ cited.question }}」
          <template v-if="citedOrdinal">—— 引了本节第 {{ citedOrdinal }} 段</template>
          <template v-else-if="cited.located"> —— 这一节里没找到被引的那一段 </template>
          <template v-else>—— 当时就没定位到原文</template>
        </span>
        <RouterLink class="trail-back" :to="`/chat/${cited.conversationId}#m-${cited.messageId}`">
          回到对话
        </RouterLink>
      </div>

      <p v-if="error" class="banner" role="alert">{{ error }}</p>

      <template v-if="chapter && prose">
        <header class="bar">
          <span class="mono facts">
            <span>第 {{ chapter.idx }}/{{ chapter.chapterCount }} 节</span>
            <span>{{ chapter.wordCount.toLocaleString() }} 字</span>
            <span v-if="prose.footnotes.size">脚注 {{ prose.footnotes.size }} 条</span>
          </span>
          <span class="grow" />
          <button class="act" type="button" @click="showSource = !showSource">
            <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">
              <path d="M9 7l-5 5 5 5" />
              <path d="M15 7l5 5-5 5" />
            </svg>
            <span class="mono">{{ showSource ? '看排版后的正文' : '看 Markdown 源' }}</span>
          </button>
          <!-- 「就这一节提问」是 S7 的内容。点了没反应的按钮比暂时没有按钮更糟。 -->
        </header>

        <div ref="scroller" class="scroller">
          <pre v-if="showSource" class="mono source">{{ chapter.markdown }}</pre>
          <ChapterProse
            v-else
            :blocks="prose.blocks"
            :footnotes="prose.footnotes"
            :images="images"
            :highlight="highlight"
          />
        </div>

        <footer class="foot">
          <button
            class="step"
            type="button"
            :disabled="chapter.prevIdx === null"
            @click="go(chapter.prevIdx)"
          >
            <span class="mono">← {{ titleOf(chapter.prevIdx) || '已是第一节' }}</span>
          </button>
          <span class="grow" />
          <span class="mono path" :title="chapter.sourcePath">{{ chapter.sourcePath }}</span>
          <span class="grow" />
          <button
            class="step step--next"
            type="button"
            :disabled="chapter.nextIdx === null"
            @click="go(chapter.nextIdx)"
          >
            <span class="mono">{{ titleOf(chapter.nextIdx) || '已是最后一节' }} →</span>
          </button>
        </footer>
      </template>
    </main>
  </div>
</template>

<style scoped>
/* 朱批的淡底。整条横条都是「人做的事」那一侧的颜色 */
.trail {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  padding: 13px 36px;
  background: var(--mark-bg);
  border-bottom: 1px solid #e0bfbb;
}

.trail-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  stroke: var(--mark);
}

.trail-cap {
  flex-shrink: 0;
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--mark);
}

.trail-text {
  flex-grow: 1;
  min-width: 0;
  font-size: 12.5px;
  color: #7e2a22;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trail-back {
  flex-shrink: 0;
  padding: 5px 13px;
  background: var(--mark);
  color: var(--surface);
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
}

.shell {
  display: flex;
  height: 100%;
  background: var(--paper);
  overflow: hidden;
}

.main {
  flex-grow: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.banner {
  margin: 30px 36px;
  padding: 12px 16px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
  line-height: 1.8;
}

.bar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 13px 36px;
  border-bottom: 1px solid var(--rule);
}

/* 分隔靠 gap 排，不靠全角空格 —— U+3000 看不见，却会跟着复制粘贴跑 */
.facts {
  display: flex;
  gap: 14px;
  font-size: 10.5px;
  letter-spacing: 0.08em;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.grow {
  flex-grow: 1;
}

.act {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 28px;
  padding: 0 12px;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
  color: var(--text-secondary);
  font-size: 11px;
}

.act:hover {
  border-color: var(--mark);
  color: var(--mark);
}

.act svg {
  width: 12px;
  height: 12px;
  stroke: currentColor;
}

.scroller {
  flex-grow: 1;
  padding: 30px 36px 40px;
  overflow-y: auto;
}

/* 源码视图刻意不做高亮：它要证明的恰恰是「这就是一个普通文本文件」 */
.source {
  max-width: 690px;
  margin: 0;
  padding: 16px 18px;
  background: var(--surface);
  border: 1px solid var(--edge);
  font-size: 12px;
  line-height: 1.95;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.foot {
  display: flex;
  align-items: center;
  padding: 12px 36px;
  border-top: 1px solid var(--rule);
  background: var(--panel);
}

.step {
  padding: 0;
  background: none;
  border: none;
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.step:disabled {
  color: var(--text-faint);
}

.step:not(:disabled):hover {
  color: var(--mark);
}

.path {
  flex-shrink: 1;
  min-width: 0;
  font-size: 10.5px;
  color: var(--text-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
