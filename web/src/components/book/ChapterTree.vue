<script setup lang="ts">
/* 目录树 + 字数条（界面稿④屏右半）。
 *
 * **字数条是质检工具，不是装饰。** 一眼扫下去，哪一节短得不像正文就露出来了——
 * 那可能是分部标题页（没事），也可能是这一节的正文压根没抽出来（有事）。
 * 系统分不清这两件事，所以它的职责是标出来让人看，不是替人下结论。
 *
 * 标出来用黄檗：那是「等人判断的地方」的颜色，不是「出错了」。
 */
import { computed } from 'vue'

import type { Chapter } from '@/types'

const props = defineProps<{ chapters: Chapter[] }>()
defineEmits<{ open: [chapter: Chapter] }>()

const longest = computed(() => Math.max(1, ...props.chapters.map((c) => c.wordCount)))

function width(chapter: Chapter): string {
  return `${Math.round((chapter.wordCount / longest.value) * 100)}%`
}
</script>

<template>
  <div class="tree">
    <button
      v-for="chapter in chapters"
      :key="chapter.id"
      type="button"
      class="row"
      :class="{ 'row--short': chapter.short, 'row--blocked': chapter.blocked }"
      @click="$emit('open', chapter)"
    >
      <span
        class="title"
        :class="{ 'title--part': chapter.level === 1 && chapter.short }"
        :style="{ paddingLeft: `${(chapter.level - 1) * 22}px` }"
      >
        {{ chapter.title || `第 ${chapter.idx} 节` }}
      </span>
      <span class="track">
        <span class="fill" :style="{ width: width(chapter) }" />
      </span>
      <span v-if="chapter.blocked" class="mono flag">被审核拦过</span>
      <span class="mono count">{{ chapter.wordCount.toLocaleString() }}</span>
    </button>
  </div>
</template>

<style scoped>
.tree {
  display: flex;
  flex-direction: column;
}

.row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 4px;
  background: none;
  border: none;
  border-bottom: 1px solid var(--hair);
  text-align: left;
  font-size: 13px;
  color: var(--text);
}

.row:hover {
  background: var(--surface);
}

/* 黄檗 = 等人判断的地方。不是「错了」，是「你看一眼」。 */
.row--short {
  background: var(--doubt-bg);
}

/* 重墨 = 停下来了。这一节送出去会被供应商的审核整段拒绝，
   不是「可疑」，是「这条路走不通」。整行压墨底而不是换第二种红：
   红在这套视觉里归「人做的标记」，借去表示阻断会把两件事搅在一起。 */
.row--blocked {
  background: var(--ink);
  color: var(--ink-text);
}

.row--blocked .fill {
  background: var(--doubt-on-dark);
}

.row--blocked .count,
.row--blocked .flag {
  color: var(--doubt-on-dark);
}

.flag {
  font-size: 10px;
  flex-shrink: 0;
}

.row--short .count {
  color: var(--doubt);
  font-weight: 500;
}

.title {
  width: 220px;
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.title--part {
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 13.5px;
}

.track {
  flex-grow: 1;
  height: 4px;
  background: var(--hair);
}

.fill {
  display: block;
  height: 100%;
  background: #8e9aa8;
}

.count {
  width: 58px;
  flex-shrink: 0;
  text-align: right;
  font-size: 11.5px;
  color: var(--text-secondary);
}
</style>
