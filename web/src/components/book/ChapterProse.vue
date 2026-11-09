<script setup lang="ts">
/* 正文与就地展开的脚注（界面稿⑤屏中段）。
 *
 * 脚注的注文**长在它出现的那一段下面**，不在页尾。这是这一屏唯一一处「改动了
 * 原文排布」的地方，理由很具体：读到「阿特曼[3]」时想知道那是什么，跳到页尾
 * 再跳回来，这一下就把一段话读断了。注文本身一个字没改，只是换了个位置出现。
 *
 * 正文其余部分是 Markdown 原样渲染。全角空格缩进与行首 4 空格是这个项目反复
 * 踩的两个坑（见 CLAUDE.md），它们都要到这一步才现形——前者会显示成一段
 * 莫名其妙的缩进，后者会被渲染器当成代码块。引擎那边已经清过，后端有测试守着。
 */
import { computed, ref, watch } from 'vue'

import { withImages, type ProseBlock } from '@/lib/markdown'

const props = defineProps<{
  blocks: ProseBlock[]
  footnotes: Map<string, string>
  /**
   * 书里的图：文件名 → 页面取回来的 blob: 地址。还没取到的那几张先只有说明文字。
   *
   * 单独一个 prop 而不是把地址直接填进 blocks：换了 blocks 就会收起展开的脚注，
   * 图晚到一步不该把读者刚点开的注收回去。
   */
  images?: Map<string, string>
  /**
   * 被引的那一块（从对话跳过来时）。-1 或不给就不标。
   *
   * 朱批色 —— 这是**人**的标记：是读者提的那个问题把这一段挑出来的。
   * 不与存疑的黄檗、阻断的重墨混用，那两种颜色说的是「系统要你判断」和
   * 「系统停下了」，跟这里不是一回事。
   */
  highlight?: number
}>()

const open = ref(new Set<string>())

// 换了一节就把展开状态清掉：编号是按节从 1 数的，留着会张冠李戴
watch(
  () => props.blocks,
  () => {
    open.value = new Set()
  }
)

function toggle(event: MouseEvent) {
  const marker = (event.target as HTMLElement).closest<HTMLElement>('.fn-ref')
  const id = marker?.dataset.fn
  if (!id) return
  const next = new Set(open.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  open.value = next
}

/** 这一块下面该挂哪几张卡。同一条注在一段里引了两次只出一张。 */
function cardsIn(block: ProseBlock): string[] {
  return [...new Set(block.refs)].filter((id) => open.value.has(id))
}

const shown = computed(() => withImages(props.blocks, props.images ?? new Map()))

const hint = computed(() => (props.footnotes.size ? '点 [n] 展开脚注' : ''))
</script>

<template>
  <div class="prose" @click="toggle">
    <template v-for="(block, i) in shown" :key="i">
      <!-- eslint-disable vue/no-v-html -- 渲染器 html:false，原文里的标签只会被转义 -->
      <div
        class="block"
        :class="{ 'block--cited': i === highlight }"
        :data-block="i"
        v-html="block.html"
      />
      <!-- eslint-enable vue/no-v-html -->

      <div v-for="id in cardsIn(block)" :key="`${i}-${id}`" class="note">
        <span class="mono label">[{{ id }}]</span>
        <div class="note-body">
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="note-text" v-html="footnotes.get(id)" />
          <span class="mono provenance">原书注</span>
        </div>
      </div>
    </template>

    <p v-if="hint" class="mono tail-hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.prose {
  max-width: 690px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

/* 朱批 = 人的标记。左栏那一道说的是「你问的那个问题引的就是这一段」 */
.block--cited {
  margin-left: -16px;
  padding-left: 14px;
  border-left: 2px solid var(--mark);
  background: var(--mark-bg);
}

/* v-html 进来的内容不带 scoped 属性，要用 :deep 才够得着 */
.prose :deep(h1) {
  margin: 0;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 25px;
  letter-spacing: 0.08em;
}

.prose :deep(h1)::after {
  content: '';
  display: block;
  width: 34px;
  height: 2px;
  margin-top: 18px;
  background: var(--mark);
}

.prose :deep(h2),
.prose :deep(h3),
.prose :deep(h4) {
  margin: 8px 0 0;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 17px;
  letter-spacing: 0.04em;
}

/* 只管书的段落（.block 里 v-html 进来的）。写成 .prose 的话，这个组件自己的
   <p class="tail-hint"> 也在里面，而 .prose[data-v] p 比 .tail-hint[data-v] 高一档——
   那行浅灰小字的提示就被排成了正文，看上去像书里写的 */
.block :deep(p) {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 15px;
  line-height: 2.1;
  color: var(--text-prose);
  text-wrap: pretty;
}

/* 章节文件第二行那句「悉达多 · 第 3/16 节 · 3,710 字」是文件自己写的，
   不是我们加的题注 —— 所以它留在正文里，只是排得轻一些。 */
.prose :deep(blockquote) {
  margin: 0;
  padding-left: 14px;
  border-left: 2px solid var(--hair);
}

.prose :deep(blockquote p) {
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.9;
  color: var(--text-muted);
}

.prose :deep(ul),
.prose :deep(ol) {
  margin: 0;
  padding-left: 26px;
  font-family: var(--font-serif);
  font-size: 15px;
  line-height: 2.1;
  color: var(--text-prose);
}

.prose :deep(img) {
  max-width: 100%;
  height: auto;
}

/* 外链图不去取，只留原书写的说明文字。排轻一点：它是图的位置，不是正文 */
.prose :deep(.img-alt) {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-faint);
}

.prose :deep(pre) {
  padding: 12px 14px;
  background: var(--surface);
  border: 1px solid var(--edge);
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 12px;
}

.prose :deep(hr) {
  width: 100%;
  height: 1px;
  margin: 6px 0;
  border: none;
  background: var(--rule);
}

/* 脚注标记：朱批色，因为它是一处「有人写过东西」的标记 */
.prose :deep(.fn-ref) {
  padding: 0 1px;
  background: none;
  border: none;
  vertical-align: super;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--mark);
  cursor: pointer;
}

.prose :deep(.fn-ref:hover) {
  background: var(--mark-bg);
}

.note {
  display: flex;
  gap: 12px;
  max-width: 540px;
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.label {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--mark);
}

.note-body {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.note-text :deep(p) {
  margin: 0;
  font-family: var(--font-sans);
  font-size: 12.5px;
  line-height: 1.85;
  color: var(--text-secondary);
}

.provenance {
  font-size: 10px;
  color: var(--text-faint);
}

.tail-hint {
  margin: 4px 0 0;
  font-size: 10.5px;
  color: var(--text-faint);
}
</style>
