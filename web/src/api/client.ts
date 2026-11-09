/* 唯一的请求出口。组件、store、composable 都不直接 fetch。
 *
 * 职责五项：注入 Authorization、按需注入 X-Brain-Key（白名单）、401 时用
 * refresh_token 续期并重放一次、把非 2xx 转成带 detail 与 code 的错误抛出，
 * 以及几种取法——JSON（`request`，上传是它的变体 `requestUpload`）、
 * 二进制（`requestBlob`）、流（`requestStream`）。
 *
 * 前四项只在 `send` 里写一遍。流原先在 `useSSE` 里另写了一份，少的正是续期：
 * access token 半小时过期，闲置半小时再问，别的请求都续得上，偏偏提问失败。
 *
 * 用原生 fetch 不用 axios：本项目两条最关键的链路（解析进度、对话输出）都是
 * SSE，axios 不支持流式读取，仍得用 fetch。与其两套并存，不如只用一套。
 */

import { readBrainKey } from '@/lib/brainKeys'

const TOKEN_KEY = 'booksoul.tokens'

export interface StoredTokens {
  accessToken: string
  refreshToken: string
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /**
     * 服务端给的机读标识，多数错误没有。
     *
     * 有它的地方是「同一个状态码下有两种必须区别对待的情况」——比如两种 409：
     * 一种要弹「重新挑章节」，一种只要让人等一会儿。靠中文文案分辨它们，
     * 等于把界面逻辑挂在一句随时会被润色的话上，而且改坏了不报错。
     */
    readonly code: string = ''
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function readTokens(): StoredTokens | null {
  try {
    const raw = localStorage.getItem(TOKEN_KEY)
    return raw ? (JSON.parse(raw) as StoredTokens) : null
  } catch {
    // 隐私模式、被清过的站点数据、禁写 storage 的浏览器都会走到这里。
    // 读不到就是没登录，不该让整个页面崩掉。
    return null
  }
}

export function writeTokens(tokens: StoredTokens | null): void {
  try {
    if (tokens) localStorage.setItem(TOKEN_KEY, JSON.stringify(tokens))
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // 存不下就退化成「这次会话内有效」，刷新后要重登——比整个登录流程炸掉好。
  }
}

/** 续期也续不上时调它：清掉登录态、回登录页，由 router 那边接手。 */
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

async function toError(response: Response): Promise<ApiError> {
  let detail = `请求失败（${response.status}）`
  let code = ''
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') detail = body.detail
    else if (Array.isArray(body?.detail) && body.detail[0]?.msg) detail = body.detail[0].msg
    if (typeof body?.code === 'string') code = body.code
  } catch {
    // 响应体不是 JSON（网关错误页之类），保留上面那句带状态码的兜底
  }
  return new ApiError(response.status, detail, code)
}

async function refreshTokens(): Promise<boolean> {
  const tokens = readTokens()
  if (!tokens?.refreshToken) return false

  const response = await fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refreshToken: tokens.refreshToken })
  })
  if (!response.ok) return false

  const fresh = await response.json()
  writeTokens({ accessToken: fresh.accessToken, refreshToken: fresh.refreshToken })
  return true
}

interface SendInit {
  method?: string
  body?: BodyInit
  headers?: Record<string, string>
  /**
   * 要用哪个大脑的 key。给了名字，就从 localStorage 取出来放进 `X-Brain-Key` 头。
   *
   * **key 只走请求头这一条路。** 不进 URL（会被记进各级访问日志、浏览器历史、
   * Referer），不进请求体（会被任何一个「把 body 打出来看看」的调试代码抄走）。
   * 取值这件事也只在这里做一次 —— 调用方拿不到 key，也就没机会把它放到别处。
   *
   * **这就是注入 key 的白名单**：只有显式带了 `brain` 的调用才会带上 key。
   * 目前两处——提问与开会话（`useAsk`）、⑧屏「列出模型」（`api/brains.listModels`）。
   * 别的请求一律不带，哪怕顺手也不行：多带一处就多一个会被记下来的地方。
   */
  brain?: string
  signal?: AbortSignal
}

/**
 * 发一个请求。带上 token（与按需的 key）；401 就续期、重放一次，续不上就清掉
 * token、交给登录页；非 2xx 一律抛 `ApiError`。
 *
 * 重放对提问（POST 的流）也是安全的：401 出在认证那一步，服务端还没写下提问、
 * 更没调模型。这和「流断了不自动重发」不是一回事——那时钱已经花出去了。
 */
async function send(path: string, init: SendInit = {}, retried = false): Promise<Response> {
  const headers: Record<string, string> = { ...init.headers }
  const tokens = readTokens()
  if (tokens?.accessToken) headers.Authorization = `Bearer ${tokens.accessToken}`
  if (init.brain) {
    const key = readBrainKey(init.brain)
    if (key) headers['X-Brain-Key'] = key
  }

  const response = await fetch(path, {
    method: init.method ?? 'GET',
    headers,
    body: init.body,
    signal: init.signal
  })

  if (response.status === 401 && !retried) {
    if (await refreshTokens()) return send(path, init, true)
    writeTokens(null)
    onUnauthorized?.()
  }
  if (!response.ok) throw await toError(response)
  return response
}

function json(body: unknown): Pick<SendInit, 'body' | 'headers'> {
  if (body === undefined) return {}
  return { body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }
}

interface RequestOptions {
  method?: string
  body?: unknown
  /** 见 `SendInit.brain`：白名单，只有显式给了才带 key。 */
  brain?: string
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, brain } = options
  const response = await send(path, { method, brain, ...json(body) })
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** 上传用。不设 Content-Type —— 让浏览器自己带上 multipart 的 boundary。 */
export async function requestUpload<T>(path: string, form: FormData): Promise<T> {
  const response = await send(path, { method: 'POST', body: form })
  return (await response.json()) as T
}

/**
 * 二进制响应（书里的图）。`<img src>` 带不上 Authorization 头，只能带着 token
 * 取成 Blob，再交给 `URL.createObjectURL`。
 */
export async function requestBlob(path: string): Promise<Blob> {
  return (await send(path)).blob()
}

export interface StreamOptions extends RequestOptions {
  signal?: AbortSignal
}

/**
 * 流（SSE）。返回响应本身：头在这一刻就到了（开会话的新地址在 `Location` 里），
 * 事件由调用方按 `\n\n` 切开读（`composables/useSSE`）。
 *
 * **不用 `EventSource`**：它不支持自定义请求头，而这里要带 `Authorization`
 * （对话那条流还要带 `X-Brain-Key`）。
 */
export async function requestStream(path: string, options: StreamOptions = {}): Promise<Response> {
  const { method = 'GET', body, brain, signal } = options
  const init = json(body)
  const response = await send(path, {
    method,
    brain,
    signal,
    body: init.body,
    headers: { ...init.headers, Accept: 'text/event-stream' }
  })
  if (!response.body) throw new ApiError(response.status, `这条流打不开（${response.status}）`)
  return response
}
