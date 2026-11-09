/* 与后端 Pydantic schema 对应的类型。
 *
 * 字段一律 camelCase，与 API JSON 一致，前端不再转换——转换只发生在后端
 * Pydantic 序列化那一处（见 backend.md「跨层命名对照」）。
 */

export interface TokenPair {
  accessToken: string
  refreshToken: string
  tokenType: string
}

export interface User {
  id: number
  email: string
  displayName: string
}

export type BookStatus = 'parsing' | 'ready' | 'failed'

export interface Book {
  id: number
  title: string
  author: string
  status: BookStatus
  sourceFormat: string
  chapterCount: number
  wordCount: number
  /** 未核对的存疑条数。解析成功不等于解析正确。 */
  noteCount: number
  /** 最近一次解析的错误码，空串表示没出错。中文短语由前端映射。 */
  errorCode: string
  createdAt: string
}

export interface UploadAccepted {
  bookId: number
  jobId: number
}

export type ParseStage = 'read' | 'toc' | 'convert' | 'clean' | 'write'

export interface Job {
  id: number
  bookId: number
  stage: ParseStage
  progressDone: number
  progressTotal: number
  detectedFormat: string
  errorCode: string
  errorMessage: string
  remedy: string
  /** 清洗账本：脚注接上几条、图片几张、多少节多少字。 */
  cleanupStats: Record<string, number>
  startedAt: string | null
  finishedAt: string | null
}

/** 一本书此刻的状态（`GET /api/books/{id}/state`），连同最近一次解析任务。 */
export interface BookState {
  bookId: number
  status: BookStatus
  /** 有没有上一次成功的那一版：重解析中、重解析失败之后照读它 */
  published: boolean
  jobId: number | null
  /** 从没排过解析时为空串 */
  stage: ParseStage | ''
  done: number
  total: number
  errorCode: string
  /** 给人看的那一句，服务端码表里的；在跑或成功时为空串 */
  message: string
  remedy: string
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  size: number
}

export interface Chapter {
  id: number
  idx: number
  title: string
  level: number
  wordCount: number
  filePath: string
  /** 短到该让人看一眼吗。字数条不只是统计，是质检工具。 */
  short: boolean
  /**
   * 这一节被内容审核点名了（S13）。
   *
   * **只有嫌疑收窄到它一节时才为真。** 分不出是哪一节就一节都不挂——
   * 随便挂一个等于凭空指认，用户会照着去删一节没问题的正文。
   */
  blocked: boolean
}

/**
 * 这本书被内容审核拦过（界面稿④屏那条重墨提示）。
 *
 * 没被拦过时整个字段是 null，不是一个「0 次」的空对象：**成功不给颜色**，
 * 没被拦是默认状态，界面上不该有东西替它占位置。
 */
export interface Moderation {
  times: number
  brainName: string
  at: string
  /** 每次被拦时都在场的那几节 */
  suspectChapterIds: number[]
  suspectTitles: string[]
  /** 收窄到一节了吗。只有它为真，目录树上才挂重墨。 */
  pinned: boolean
  /** 一句能照着做的话：定位到了就点名，没定位到就说怎么缩小范围 */
  advice: string
}

export interface Note {
  id: number
  chapterId: number | null
  severity: 'doubt' | 'block'
  code: string
  message: string
  resolved: boolean
  createdAt: string
}

/** 正文额度从哪来。数字要带着来路走：猜的数和查得到的数长得一模一样。 */
export type ContextSource = 'vendor' | 'manual' | 'builtin' | 'estimate'

export interface BrainFit {
  name: string
  model: string
  contextChars: number
  contextSource: ContextSource
  /** null = 窗口是估计的，不下结论 */
  fits: boolean | null
}

export interface BookDetail {
  id: number
  title: string
  author: string
  translator: string
  contributor: string
  publisher: string
  published: string
  isbn: string
  language: string
  description: string
  sourceFile: string
  sourceFormat: string
  storagePath: string | null
  chapterCount: number
  wordCount: number
  status: BookStatus
  createdAt: string
  /**
   * 有几个会话跟这本书聊过。
   *
   * 「重新解析」按它决定要不要先问一句：重解析会清掉这些会话锁定的章节，
   * 下一轮要重挑一次、缓存失效、输入按全价重算，旧引文还要重新定位。
   */
  conversationCount: number
  /** 被内容审核拦过的账。没被拦过就是 null。 */
  moderation: Moderation | null
  chapters: Chapter[]
  notes: Note[]
  brains: BrainFit[]
}

export interface LedgerRow {
  label: string
  detail: string
}

export interface OutputFile {
  path: string
  sizeBytes: number
  isDir: boolean
  childCount: number
}

export interface Report {
  bookId: number
  title: string
  sourceFile: string
  sourceSizeBytes: number
  elapsedSeconds: number | null
  ledger: LedgerRow[]
  stats: Record<string, string | number>
  outputs: OutputFile[]
  storagePath: string | null
  notes: Note[]
}

export interface ChapterContent {
  bookId: number
  bookTitle: string
  idx: number
  title: string
  level: number
  wordCount: number
  /** 相对书目录，如 chapters/003-婆罗门之子.md */
  filePath: string
  /** 从书库根起算的完整相对路径，照着它能在磁盘上找到这个文件 */
  sourcePath: string
  /** 磁盘上那个文件的原文，一个字没动 */
  markdown: string
  chapterCount: number
  /** 前面各节字数的和 —— 真加出来的，不是按节数估的 */
  precedingWords: number
  prevIdx: number | null
  nextIdx: number | null
}

export interface Brain {
  /** 内置预设不在库里，没有 id */
  id: number | null
  name: string
  provider: string
  /** 实际在用的模型：内置预设 = 你的选择（没选过就是预设的） */
  model: string
  /** 预设本来的模型，「恢复默认」回到它；自定义端点是空串 */
  defaultModel: string
  /** 给这个内置预设换过模型 —— 为真时才出现「恢复默认」 */
  modelChosen: boolean
  baseUrl: string
  /** 环境变量的**名字**（DASHSCOPE_API_KEY），不是 key 本身。响应里永远没有 key。 */
  apiKeyEnv: string
  note: string
  /** 正文额度（字），不是厂商公布的窗口（token）。选章用的是同一个数。 */
  contextChars: number
  /** 存下来的窗口（token）：供应商报的或你填的。内置表 / 估计时为 0（那两种只有字数） */
  contextTokens: number
  contextSource: ContextSource
  needsKey: boolean
  custom: boolean
  /** 只有请求带了 bookId 时才有；窗口是估计的那一行也是 null（不下结论） */
  fits: boolean | null
  fitNote: string
}

/** 这个 key 在这家能用的一个模型。只有 id 与窗口 —— 对方响应里的其他字段服务端不转回来。 */
export interface ModelEntry {
  id: string
  /** 供应商在列表里报了窗口（token）才有；0 = 没报 */
  contextTokens: number
}

export interface ModelList {
  /** 服务端排好的：对话类在前、明显不是对话的沉底，各档保持供应商原序 */
  items: ModelEntry[]
  /** 被问的那个地址：「共 N 个，来自 {source}」 */
  source: string
}

/** 给一个大脑换模型（PUT /api/brains/{name}/model）。 */
export interface ModelChoice {
  model: string
  /** 上下文窗口（token）；0 = 不知道 */
  contextTokens: number
  /** 窗口的来路；窗口为 0 时必须为空 */
  contextSource: '' | 'vendor' | 'manual'
}

export interface BrainCreate {
  name: string
  model: string
  baseUrl: string
  contextTokens: number
  note: string
  apiKeyEnv: string
}

export type PersonaKey = 'scholar' | 'author' | 'book'

export interface ChatMessage {
  id: number
  seq: number
  turn: number
  role: 'user' | 'assistant'
  /** user 消息为 null；assistant 消息记它是哪一路人格 */
  persona: PersonaKey | null
  content: string
  /** false = 断流留下的残文。界面要说「这条回答没说完」，不能当完整回答显示。 */
  complete: boolean
  inputTokens: number
  outputTokens: number
  cacheRead: number
  cacheWrite: number
  createdAt: string
  /** 这条回答引了哪些原文（S9 溯源）。跟着会话详情一起来，不必逐条再问一次。 */
  citations: Citation[]
}

/**
 * 这一轮有什么不一样（SSE `notice`），比如「这本书重新解析过了，这次按全价算」。
 *
 * 和 `stage` 不是一回事：`stage` 是「现在在做什么」，下一句就盖掉上一句；
 * 告示一条都不能丢。`text` 是服务端写好的那句话，照样显示；要分支就看 `code`。
 */
export interface TurnNotice {
  code: string
  text: string
}

/**
 * 一种人格的提示词（⑧屏右栏）。
 *
 * 列表里 `text` 是空串，点开哪个才去取哪个的正文——三段加起来六千多字，
 * 没有理由在进页面那一下全背着走。
 */
export interface Persona {
  key: PersonaKey
  name: string
  description: string
  /** 磁盘上那个文件，形如 `prompts/scholar.md`。这三个 md 也能用编辑器直接改。 */
  filename: string
  text: string
  /** 有没有从界面改过。false 时「还原」没有可还原的东西。 */
  edited: boolean
}

export interface Conversation {
  id: number
  bookId: number
  bookTitle: string
  persona: PersonaKey
  brainName: string
  model: string
  /** 第一个问题，用作会话标题 */
  title: string
  reflection: boolean
  turnCount: number
  createdAt: string
  updatedAt: string
  messages: ChatMessage[]
  bookWordCount: number
  bookChapterCount: number
  /** 当下的选章结论。null = 还没问过。整张排名表要单独拉⑥屏那条。 */
  retrieval: RetrievalBrief | null
}

export interface RetrievalBrief {
  turn: number
  selectedCount: number
  totalCount: number
  /** true = 整本都给了模型，没跑 BM25 */
  wholeBook: boolean
  budgetChars: number
  usedChars: number
}

export interface MatchedTerm {
  term: string
  count: number
}

export interface RetrievalItem {
  /** 重解析之后会变成 null；下面两列是当时的快照，那时靠它们认人 */
  chapterId: number | null
  chapterIdx: number
  chapterTitle: string
  rank: number
  score: number
  wordCount: number
  selected: boolean
  /** 按名次累加到这一节为止用掉的字数；没选中的为 0，界面上画破折号 */
  cumulativeChars: number
  matchedTerms: MatchedTerm[]
}

export interface Shortfall {
  title: string
  need: number
  left: number
  short: number
}

export interface Retrieval {
  conversationId: number
  turn: number
  /** 是这句话挑的章。后续轮次复用这份锁定，所以它未必是最近那个问题。 */
  question: string
  bookTitle: string
  model: string
  budgetChars: number
  usedChars: number
  selectedCount: number
  totalCount: number
  /** true 时 score 那一列恒为 0 —— 没跑 BM25，界面必须说破 */
  wholeBook: boolean
  queryTerms: string[]
  items: RetrievalItem[]
  shortfall: Shortfall | null
  createdAt: string
}

export interface Citation {
  id: number
  messageId: number
  quote: string
  /** -1 = 没定位到。**一律看 `located`**，不要自己拿它跟 0 比：
   *  写成 `paraIndex > 0` 的那天，第 0 块里的引文会集体变成「找不到」。 */
  paraIndex: number
  located: boolean
  /** 被 S12 重定位过（重解析之后重新找回来的），值得跟用户说一声 */
  relocated: boolean
  chapterId: number | null
  /** 阅读页路由要它。重解析之后为 null，那时只剩 chapterTitle 这个快照 */
  chapterIdx: number | null
  chapterTitle: string
  bookId: number
  conversationId: number
  /** 是哪个问题把人带到这一段的 —— ⑤屏那条来路条要说的就是它 */
  question: string
  createdAt: string
}
