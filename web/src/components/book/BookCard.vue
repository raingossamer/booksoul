<script setup lang="ts">
/* 书库卡片（界面稿③屏），四种状态。
 *
 * 色彩按语义走，不是挑好看的：
 *   解析中 → 靛（机器在做事），浅底 + 虚线，不用实心书脊
 *   存疑   → 黄檗（等人判断），书脊照旧，只把强调色换掉并在脚注挂上条数
 *   停下了 → 重墨（阻断），整块墨底
 *   就绪   → **不着色**。成功是默认状态，只显示数字，不给绿对勾。
 *
 * 就绪态的书脊底色按 id 取模轮换，纯装饰——书架上一排同色的书很难扫读。
 * 每种书脊只设一个 `--accent`，格式标签与那道短横都跟着它走，
 * 存疑时整体换掉这一个变量就够了。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import type { Book } from '@/types'

const props = defineProps<{ book: Book }>()
defineEmits<{ reparse: [id: number] }>()

/** 英文错误码 → 中文短语。枚举值入库一律英文，中文展示名在前端映射。 */
const FAILURE_TEXT: Record<string, { badge: string; caption: string }> = {
  no_text_layer: { badge: '需先 OCR', caption: '扫描件 · 抽不出文字' },
  bad_archive: { badge: '文件损坏', caption: '读不开 · 多半是下载中断' },
  no_content: { badge: '没有正文', caption: '抽不出任何正文' },
  unsupported_format: { badge: '格式不支持', caption: '这里暂不支持 · 换个格式再传' },
  internal: { badge: '没能完成', caption: '服务器这边出了错 · 可以再解析一次' }
}
const FAILURE_FALLBACK = { badge: '停下了', caption: '解析没能完成' }

/**
 * 重新解析帮不上忙的码：这个部署没装那种格式的解析后端，原样重跑一遍还是一样。
 * 那条出路走不通，就不摆那个按钮——出路写在说明里（换个格式再传）。
 */
const NO_RETRY = new Set(['unsupported_format'])

const spineClass = computed(() => `spine-${(props.book.id % 3) + 1}`)
const failure = computed(() => FAILURE_TEXT[props.book.errorCode] ?? FAILURE_FALLBACK)
const retryable = computed(() => !NO_RETRY.has(props.book.errorCode))
const formatLabel = computed(() => (props.book.sourceFormat || '').toUpperCase())
const counts = computed(
  () => `${props.book.chapterCount} 节 · ${props.book.wordCount.toLocaleString()} 字`
)
</script>

<template>
  <component
    :is="book.status === 'failed' ? 'article' : RouterLink"
    class="card"
    :to="book.status === 'failed' ? undefined : `/books/${book.id}`"
  >
    <!-- 停下了 -->
    <div v-if="book.status === 'failed'" class="face face--blocked">
      <span class="mono fmt">{{ formatLabel }}</span>
      <div class="stack">
        <span class="title title--dim">{{ book.title }}</span>
        <span class="mono badge">{{ failure.badge }}</span>
      </div>
    </div>

    <!-- 解析中 -->
    <div v-else-if="book.status === 'parsing'" class="face face--working">
      <span class="mono fmt">{{ formatLabel || '识别中' }}</span>
      <div class="stack">
        <span class="title title--dim">{{ book.title }}</span>
        <div class="bar"><div class="bar-fill" /></div>
      </div>
    </div>

    <!-- 就绪（可能带存疑） -->
    <div v-else class="face" :class="[spineClass, { 'face--doubt': book.noteCount > 0 }]">
      <span class="mono fmt">{{ formatLabel }}</span>
      <div class="stack">
        <span class="title">{{ book.title }}</span>
        <div class="rule" />
        <span class="byline">{{ book.author || '未标注作者' }}</span>
      </div>
    </div>

    <div class="caption">
      <span class="name" :class="{ 'name--dim': book.status !== 'ready' }">
        {{ book.title }}
      </span>
      <span v-if="book.status === 'failed'" class="failed-note">
        <span class="mono note note--blocked">{{ failure.caption }}</span>
        <!-- 停下来必须给出路。没有这个按钮，那本书就只是躺在书库里报错；
             重跑一遍也还是一样的那几种，出路在上面那句说明里 -->
        <button
          v-if="retryable"
          class="mono retry"
          type="button"
          @click="$emit('reparse', book.id)"
        >
          重新解析
        </button>
      </span>
      <span v-else-if="book.status === 'parsing'" class="mono note note--machine"> 解析中 </span>
      <span v-else class="stats">
        <span class="mono note">{{ counts }}</span>
        <span v-if="book.noteCount > 0" class="mono note note--doubt">
          {{ book.noteCount }} 处存疑
        </span>
      </span>
    </div>
  </component>
</template>

<style scoped>
.card {
  display: flex;
  flex-direction: column;
  gap: 11px;
  text-decoration: none;
  color: inherit;
}

.face {
  height: 300px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 20px 18px;
  /* 每种状态只改这一个变量，格式标签与短横自动跟上 */
  --accent: var(--text-muted);
}

.spine-1 {
  background: var(--spine-1);
  --accent: var(--spine-1-accent);
}

.spine-2 {
  background: var(--spine-2);
  --accent: var(--spine-2-accent);
}

.spine-3 {
  background: var(--spine-3);
  --accent: var(--spine-3-accent);
}

/* 存疑：书脊底色不动，只把强调色换成深底上的黄檗 */
.face--doubt {
  --accent: var(--doubt-on-dark);
}

.face--working {
  background: var(--surface);
  border: 1px dashed var(--machine);
  --accent: var(--machine);
}

.face--blocked {
  background: var(--ink);
}

.fmt {
  font-size: 9.5px;
  letter-spacing: 0.2em;
  color: var(--accent);
}

.stack {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.title {
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 27px;
  line-height: 1.25;
  letter-spacing: 0.06em;
  color: var(--paper);
  word-break: break-word;
}

.title--dim {
  color: #6e7887;
}

.rule {
  width: 24px;
  height: 2px;
  background: var(--accent);
}

.byline {
  font-size: 11px;
  color: var(--text-faint);
}

.badge {
  align-self: flex-start;
  padding: 3px 7px;
  background: var(--ink-text);
  color: var(--ink);
  font-size: 9.5px;
  letter-spacing: 0.12em;
}

.bar {
  height: 3px;
  background: #e2e4dc;
  overflow: hidden;
}

/* 进度未知时用一段来回走的条：S2 只知道「在解析」，
   分节粒度的真进度是 S3 接上 SSE 之后的事。 */
.bar-fill {
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

.caption {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.name {
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 14.5px;
}

.name--dim {
  color: #6e7887;
}

.stats {
  display: flex;
  align-items: center;
  gap: 7px;
}

.note {
  font-size: 10.5px;
  color: var(--text-muted);
}

.note--doubt {
  color: var(--doubt);
}

.note--machine {
  color: var(--machine);
}

.note--blocked {
  color: var(--text-secondary);
}

.failed-note {
  display: flex;
  align-items: center;
  gap: 9px;
}

.retry {
  padding: 1px 8px;
  background: none;
  border: 1px solid var(--edge-strong);
  color: var(--text-secondary);
  font-size: 10.5px;
}

.retry:hover {
  border-color: var(--mark);
  color: var(--mark);
}
</style>
