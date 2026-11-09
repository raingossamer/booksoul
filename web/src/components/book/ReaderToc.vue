<script setup lang="ts">
/* 阅读器左栏：目录 + 位置条（界面稿⑤屏左半）。
 *
 * 位置条量的是**字数**不是节数。一本书里各节长短能差二十倍，按节数画出来的
 * 进度条会告诉你「读了一半」，而其实前面那八节加起来只有全书的两成。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import type { Chapter } from '@/types'

const props = defineProps<{
  bookId: number
  title: string
  chapters: Chapter[]
  wordCount: number
  currentIdx: number
  precedingWords: number
}>()

const list = ref<HTMLElement | null>(null)

const current = computed(() => props.chapters.find((c) => c.idx === props.currentIdx))

/**
 * 目录里「你在这儿」那一项要看得见。一本书一百多节，从对话跳到第八十节时，
 * 左栏停在最上面，那道朱批标在视线之外——等于没标。
 */
async function showCurrent() {
  await nextTick()
  list.value?.querySelector('.item--here')?.scrollIntoView({ block: 'nearest' })
}

onMounted(showCurrent)
watch(() => props.currentIdx, showCurrent)

function share(words: number): string {
  if (!props.wordCount) return '0%'
  return `${(words / props.wordCount) * 100}%`
}
</script>

<template>
  <aside class="toc">
    <RouterLink class="head" :to="`/books/${bookId}`">
      <span class="book">{{ title }}</span>
      <span class="mono shape">
        {{ chapters.length }} 节 · {{ wordCount.toLocaleString() }} 字
      </span>
    </RouterLink>

    <nav ref="list" class="list">
      <RouterLink
        v-for="chapter in chapters"
        :key="chapter.id"
        class="item"
        :class="{
          'item--part': chapter.level === 1,
          'item--here': chapter.idx === currentIdx
        }"
        :style="{ paddingLeft: `${20 + (chapter.level - 1) * 14}px` }"
        :to="`/books/${bookId}/read/${chapter.idx}`"
      >
        {{ chapter.title || `第 ${chapter.idx} 节` }}
      </RouterLink>
    </nav>

    <div class="where">
      <span class="mono cap">全书位置</span>
      <div class="bar">
        <span class="before" :style="{ width: share(precedingWords) }" />
        <span class="here" :style="{ width: share(current?.wordCount ?? 0) }" />
      </div>
      <span class="mono figures">
        <span>{{ currentIdx }} / {{ chapters.length }}</span>
        <span class="dot">·</span>
        <span>前面 {{ precedingWords.toLocaleString() }} 字</span>
      </span>
    </div>
  </aside>
</template>

<style scoped>
.toc {
  width: 232px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--panel);
  border-right: 1px solid var(--rule);
}

.head {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 20px 20px 13px;
  border-bottom: 1px solid var(--edge);
  text-decoration: none;
}

.book {
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 15px;
  letter-spacing: 0.04em;
  color: var(--text);
}

.shape {
  font-size: 10.5px;
  color: var(--text-muted);
}

.list {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  padding: 9px 0;
  overflow-y: auto;
}

.item {
  padding: 5px 20px;
  font-size: 12.5px;
  color: var(--text-secondary);
  text-decoration: none;
  border-left: 2px solid transparent;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item:hover {
  background: var(--surface);
}

/* 分部标题自己就是一节（常常只有五个字），衬线加粗把它和正文节区分开 */
.item--part {
  font-family: var(--font-serif);
  font-weight: 700;
  color: var(--text);
}

/* 朱批 = 人在哪儿。当前位置是「你」留下的位置，不是机器算的 */
.item--here {
  background: var(--paper);
  border-left-color: var(--mark);
  color: var(--mark);
  font-weight: 500;
}

.where {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 12px 20px 14px;
  border-top: 1px solid var(--edge);
}

.cap {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--text-faint);
}

.dot {
  color: var(--text-faint);
}

.bar {
  display: flex;
  height: 6px;
  background: var(--hair);
}

.before {
  flex-shrink: 0;
}

.here {
  flex-shrink: 0;
  min-width: 2px;
  background: var(--mark);
}

/* 分隔靠 gap 排，不靠全角空格。
   源码里的 U+3000 看不见，却会跟着复制粘贴跑。 */
.figures {
  display: flex;
  gap: 6px;
  font-size: 10.5px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
</style>
