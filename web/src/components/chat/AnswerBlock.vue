<script setup lang="ts">
/* 一条回答（界面稿⑦屏的答栏）。
 *
 * 这个组件最要紧的分支是 `complete === false`：那是一条断流留下的残文。
 *
 * **半截话必须看起来像半截话。** 它读起来往往是完整的——模型说到一半停下时，
 * 最后那句话通常正好是个句号。用户会把它当成完整回答读下去，然后奇怪
 * 「怎么答得这么浅」。所以这里给它一条黄檗色的边和一句明说，
 * 再给两个按钮让用户自己决定要不要再花一次钱（Spec #38）。
 *
 * 引文块（S9 溯源）是这一屏第二要紧的东西：**回答里引的原文，要能点回去核对。**
 * 三种状态各说各的话，不混为一谈——
 * 定位到了给一条「去原文」，章节被重解析过就说「位置已变更」，
 * 当初就没找到则照直说「这句话在原文里没找到」。最后那一种是黄檗色：
 * 它不是系统出错，是一件要用户自己判断的事。
 *
 * **三种人格用填法区分身份，不借用状态色**（S10）：研读者实朱、作者实墨、
 * 书是朱框空心。颜色在这套设计里是有归属的——朱批 = 人、靛 = 机器、
 * 黄檗 = 存疑、重墨 = 阻断。拿状态色去区分三个平级的说话人，等于让「作者」
 * 这一列看起来像是出了什么事。
 *
 * 正文按 Markdown 渲，流到一半也渲：模型写的是 `###`、`>`、`**`，原样摆出来
 * 读者得自己在脑子里排一遍版。渲染器的取舍（为什么单换行算换行、为什么不认图片）
 * 写在 `lib/markdown.ts` 的 `renderAnswer` 上。
 */
import { computed } from 'vue'

import { renderAnswer } from '@/lib/markdown'
import type { ChatMessage, PersonaKey } from '@/types'

const props = defineProps<{
  message?: ChatMessage
  /** 正在流的那一段。给了它就是「这条还在说」 */
  streaming?: string
  /**
   * 还没开口时它在做什么（「正在挑章节…」）。
   *
   * 书装不下时要先切词打分，那几秒里一个 token 都没有。空白和卡死在屏幕上
   * 长得一模一样，而用户对卡死的反应是再问一遍 —— 那要再花一次钱。
   */
  stage?: string
  persona: PersonaKey
  busy?: boolean
  /** 对读的三列。窄一些、密一些，其余一模一样。 */
  column?: boolean
  /** 这一路自己的错误（对读时一列失败不该让另外两列跟着变白）。 */
  failure?: string
}>()

defineEmits<{ retry: []; resume: []; pick: [] }>()

const GLYPH: Record<PersonaKey, string> = { scholar: '研', author: '黑', book: '书' }
const LABEL: Record<PersonaKey, string> = {
  scholar: '研读者',
  author: '作者本人',
  book: '书本身'
}

const html = computed(() =>
  renderAnswer((props.streaming !== undefined ? props.streaming : props.message?.content) ?? '')
)
</script>

<template>
  <!-- id：从阅读页「回到对话」回来时，落回点「去原文」的那一条 -->
  <div
    :id="message ? `m-${message.id}` : undefined"
    class="answer"
    :class="{
      'answer--cut': message && !message.complete,
      'answer--column': column
    }"
  >
    <header class="head">
      <span class="glyph" :class="`glyph--${persona}`">{{ GLYPH[persona] }}</span>
      <span class="label">{{ LABEL[persona] }}</span>
      <span class="grow" />
      <button
        v-if="column && message"
        class="pick"
        type="button"
        :disabled="busy"
        @click="$emit('pick')"
      >
        跟这个继续聊
      </button>
      <span v-if="streaming !== undefined" class="mono live">
        {{ streaming || !stage ? '正在说…' : stage }}
      </span>
      <span v-else-if="message && message.outputTokens" class="mono usage">
        输出 {{ message.outputTokens.toLocaleString() }}
        <template v-if="message.cacheRead">
          · 缓存命中 {{ message.cacheRead.toLocaleString() }}
        </template>
      </span>
    </header>

    <p v-if="streaming !== undefined && !streaming && stage" class="waiting">
      {{ stage }}
    </p>
    <!-- eslint-disable-next-line vue/no-v-html -- 渲染器 html:false、不认图片，模型写的标签只会被转义 -->
    <div v-else-if="html" class="body" v-html="html" />

    <p v-if="failure" class="failure">{{ failure }}</p>

    <!-- 三种状态互斥，一条引文只说一句：找得到 / 原位置变了 / 当初就没找到。
         原先「重新找回」那一句另起了一条 v-if 链，平平常常定位到的引文也会落进
         「原位置已变更」那一支——「去原文」底下紧跟着一句它的位置变了 -->
    <div v-for="one in message?.citations ?? []" :key="one.id" class="cite">
      <p class="cite-quote">{{ one.quote }}</p>
      <template v-if="one.located && one.chapterIdx !== null">
        <RouterLink
          class="cite-go"
          :to="`/books/${one.bookId}/read/${one.chapterIdx}?cite=${one.id}`"
        >
          去原文 · {{ one.chapterTitle }}
        </RouterLink>
        <!-- 这本书重解析过，这条引文是事后重新找回来的。说一声，是因为它现在
             指向的是一份**和当初回答时不完全相同**的正文——用户核对时该知道。 -->
        <span v-if="one.relocated" class="mono cite-again">重解析后重新找回</span>
      </template>
      <span v-else-if="one.chapterTitle" class="cite-moved">
        原《{{ one.chapterTitle }}》里这段 · 原位置已变更
      </span>
      <span v-else class="cite-missing">这句话在这次载入的原文里没找到</span>
    </div>

    <div v-if="message && !message.complete" class="cut">
      <span class="cut-text">回答中途断了，上面这些字已经计费。</span>
      <button class="act" type="button" :disabled="busy" @click="$emit('resume')">接着往下</button>
      <button class="act" type="button" :disabled="busy" @click="$emit('retry')">重问</button>
    </div>
  </div>
</template>

<style scoped>
.answer {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--edge);
}

/* 黄檗 = 等你判断。半截回答不是错误，是一个要你决定怎么办的情况 */
.answer--cut {
  border-color: #e5d5ae;
  background: var(--doubt-bg);
}

.head {
  display: flex;
  align-items: center;
  gap: 9px;
}

/* 填法区分身份，不借用状态色：实朱 / 实墨 / 朱框空心 */
.glyph {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 11px;
}

.glyph--scholar {
  background: var(--mark);
  color: var(--surface);
}

.glyph--author {
  background: var(--terminal);
  color: var(--ink-text);
}

.glyph--book {
  background: var(--surface);
  border: 1.5px solid var(--mark);
  color: var(--mark);
}

/* 对读的一列。窄、密，其余一样 */
.answer--column {
  flex: 1;
  min-width: 0;
  padding: 12px 14px;
}

.answer--column .body {
  font-size: 13px;
  line-height: 1.95;
}

.answer--column .cite-quote {
  font-size: 12.5px;
}

.pick {
  flex-shrink: 0;
  padding: 0 0 1px;
  background: none;
  border: none;
  border-bottom: 1px solid var(--mark);
  color: var(--mark);
  font-size: 11px;
  white-space: nowrap;
}

.pick:disabled {
  border-bottom-color: var(--edge-strong);
  color: var(--text-faint);
}

/* 黄檗 = 要你判断。这一路没说完，另外两路照常 —— 不是整轮出错 */
.failure {
  margin: 0;
  padding: 8px 11px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  font-size: 11.5px;
  line-height: 1.75;
  color: #6b5218;
}

.label {
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 12px;
  color: var(--text-secondary);
}

.grow {
  flex-grow: 1;
}

.usage,
.live {
  font-size: 10px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
}

/* 靛 = 机器正在做事 */
.live {
  color: var(--machine);
}

.waiting {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--machine);
}

/* 回答正文。字号都用 em，对读那一列把 .body 调小，里面跟着一起小 */
.body {
  display: flex;
  flex-direction: column;
  gap: 0.7em;
  font-family: var(--font-serif);
  font-size: 14.5px;
  line-height: 2.05;
  color: var(--text-prose);
  overflow-wrap: anywhere;
}

/* v-html 进来的内容不带 scoped 属性，要用 :deep 才够得着 */
.body :deep(p),
.body :deep(h1),
.body :deep(h2),
.body :deep(h3),
.body :deep(h4),
.body :deep(h5),
.body :deep(h6),
.body :deep(ul),
.body :deep(ol),
.body :deep(blockquote),
.body :deep(pre),
.body :deep(table),
.body :deep(hr) {
  margin: 0;
}

.body :deep(p) {
  text-wrap: pretty;
}

.body :deep(h1),
.body :deep(h2),
.body :deep(h3),
.body :deep(h4),
.body :deep(h5),
.body :deep(h6) {
  font-weight: 700;
  font-size: 1.04em;
  line-height: 1.75;
  letter-spacing: 0.03em;
  color: var(--text);
  text-wrap: balance;
}

.body :deep(h1),
.body :deep(h2) {
  font-size: 1.14em;
}

/* 新的一节前多空一点，让「一、二、三」看得出分段；打头的那个不用 */
.body :deep(h1:not(:first-child)),
.body :deep(h2:not(:first-child)),
.body :deep(h3:not(:first-child)),
.body :deep(h4:not(:first-child)) {
  margin-top: 0.4em;
}

.body :deep(strong) {
  font-weight: 700;
  color: var(--text);
}

/* 中文没有斜体，浏览器只会把字硬拧斜。强调用着重号，这是中文自己的写法 */
.body :deep(em) {
  font-style: normal;
  text-emphasis: filled dot;
  text-emphasis-position: under right;
}

/* 模型说「我在引原文」。左栏用中性的线而不是朱批：引没引对还没核过，
   核过的那一份是下面的引文块，朱批留给它 */
.body :deep(blockquote) {
  display: flex;
  flex-direction: column;
  gap: 0.5em;
  padding-left: 14px;
  border-left: 2px solid var(--edge-strong);
  color: var(--text-secondary);
}

.body :deep(ul),
.body :deep(ol) {
  padding-left: 1.6em;
}

.body :deep(li::marker) {
  color: var(--text-faint);
}

.body :deep(a) {
  color: inherit;
  text-decoration: underline;
  text-decoration-color: var(--dash);
  text-underline-offset: 3px;
}

.body :deep(code) {
  padding: 0 3px;
  background: var(--panel);
  font-family: var(--font-mono);
  font-size: 0.86em;
}

.body :deep(pre) {
  padding: 10px 12px;
  background: var(--panel);
  border: 1px solid var(--edge);
  overflow-x: auto;
  line-height: 1.7;
}

.body :deep(pre code) {
  padding: 0;
  background: none;
}

.body :deep(hr) {
  height: 1px;
  border: none;
  background: var(--rule);
}

/* 对读一列只有三分之一宽，表格放不下就自己横着滚，不把整列撑开 */
.body :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
  font-size: 0.9em;
  line-height: 1.8;
}

.body :deep(th),
.body :deep(td) {
  padding: 4px 10px;
  border: 1px solid var(--hair);
  text-align: left;
}

.body :deep(th) {
  background: var(--panel);
  font-weight: 700;
}

/* 引文块。朱批的左栏 = 这是书里的话，不是模型的话 */
.cite {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 0 10px 15px;
  border-left: 2px solid var(--mark);
  background: var(--mark-bg);
}

.cite-quote {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 14px;
  line-height: 1.95;
  color: #7e2a22;
  white-space: pre-wrap;
}

.cite-go {
  align-self: flex-start;
  font-size: 11.5px;
  color: var(--mark);
  text-decoration: none;
  border-bottom: 1px solid var(--mark);
}

.cite-moved {
  font-size: 11px;
  color: var(--text-muted);
}

/* 黄檗 = 要你判断。引不上原文不是报错，是一件该由读者自己掂量的事 */
.cite-again {
  font-size: 10px;
  color: var(--text-faint);
}

.cite-missing {
  font-size: 11px;
  color: var(--doubt);
}

.cut {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 10px;
  border-top: 1px solid #e5d5ae;
}

.cut-text {
  flex-grow: 1;
  font-size: 11.5px;
  line-height: 1.75;
  color: #6b5218;
}

.act {
  flex-shrink: 0;
  padding: 0 0 1px;
  background: none;
  border: none;
  border-bottom: 1px solid #c9a961;
  color: var(--doubt);
  font-size: 12px;
  white-space: nowrap;
}

.act:disabled {
  border-bottom-color: var(--edge-strong);
  color: var(--text-faint);
}
</style>
