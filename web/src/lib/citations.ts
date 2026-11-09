/* 在渲好的正文里找出被引的那一块（界面稿⑤屏）。
 *
 * 服务端已经把段号算好了（`citations.para_index`）。这里为什么还要再找一次？
 *
 * 因为那个段号是**另一个语言里的另一段代码**数出来的：服务端按空行切
 * Markdown 源（`services/citations.py` 的 `paragraphs()`），这边是 markdown-it
 * 渲成 HTML 之后取 body 的直接子元素。两边现在是一致的，但它们没有任何机制
 * 保证永远一致——而不一致时不会报错，只会把高亮画在隔壁那一段上，
 * 那一段看起来同样像是出处，用户核对完反而更信了。
 *
 * 所以：**段号是快路，原文是准绳。** 先看那一块里是不是真有这句话，是就用它；
 * 不是就自己找一遍；还找不到就不画高亮，并且照实说没定位到。
 *
 * 重解析之后（S12）段号必然过期，那时走的也是这条自己找一遍的路。
 */
import type { ProseBlock } from '@/lib/markdown'

/**
 * 比对用的形态，和服务端 `_norm()` 是一对：去掉脚注标记，去掉所有空白。
 *
 * 空白整个删掉而不是合起来：中文里空白不承载意义，而模型会把一段话重新折行。
 * 删掉之后两边仍然是精确比较，只是比的是「去掉排版之后」的字。
 */
export function normalize(text: string): string {
  return text.replace(/\s+/g, '')
}

/** 一块正文里的可比对文字。脚注标记要先摘掉——原文里有，引文里通常没有。 */
export function blockText(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  doc.querySelectorAll('.fn-ref').forEach((el) => el.remove())
  return normalize(doc.body.textContent ?? '')
}

/**
 * 这句引文落在第几块。找不到返回 -1 —— 不返回「最接近的那一块」。
 *
 * 省略号只取第一截：模型用「A……B」表示中间略去了，而找块只需要认出开头。
 * 服务端那边是两截都要在同一块里按顺序出现，它管的是「算不算定位到」，
 * 比这里严；这里管的只是「画在哪一块上」。
 */
export function locateBlock(
  blocks: ProseBlock[],
  cited: { paraIndex: number; quote: string }
): number {
  const needle = normalize(cited.quote.split(/…+|\.{3,}/)[0] ?? '')
  if (!needle) return -1

  const hinted = blocks[cited.paraIndex]
  if (hinted && blockText(hinted.html).includes(needle)) return cited.paraIndex

  return blocks.findIndex((block) => blockText(block.html).includes(needle))
}

/**
 * 这一块是正文里的第几段，1 起。
 *
 * 只数段落，标题和那行元信息不算——界面上说「第 2 段」是说给人听的，
 * 而人不会把标题数成一段。这个数和 `paraIndex` 是两个东西：
 * 后者是机器用的块号，前者是给人看的序数。
 */
export function paragraphOrdinal(blocks: ProseBlock[], at: number): number {
  let n = 0
  for (let i = 0; i <= at && i < blocks.length; i += 1) {
    if (blocks[i].html.startsWith('<p')) n += 1
  }
  return n
}
