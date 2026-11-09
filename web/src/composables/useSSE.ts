import { requestStream, type StreamOptions } from '@/api/client'

/**
 * 读一条 SSE 流。
 *
 * 请求本身——token、key、401 续期、非 2xx 转 `ApiError`——在 `api/client.ts`
 * 的 `requestStream`，和别的请求同一个出口；这里只管把响应体按 `\n\n` 切成事件。
 *
 * 事件名与后端约定一致：`stage` / `notice` / `token` / `answered` / `failed` /
 * `done` / `error`。
 */
export type SseHandler = (data: Record<string, unknown>) => void

export interface SseOptions extends StreamOptions {
  /** 响应头到了、事件还没开始读。开会话那条流的新会话地址在 `Location` 里，这时就有。 */
  onOpen?: (response: Response) => void
}

export async function readSSE(
  path: string,
  handlers: Record<string, SseHandler>,
  options: SseOptions = {}
): Promise<void> {
  const { onOpen, ...request } = options
  const response = await requestStream(path, request)
  const reader = response.body?.getReader()
  if (!reader) return

  try {
    onOpen?.(response)
  } catch (e) {
    // 不读了：把连接放掉。服务端那一轮照样跑完、落库——钱已经花了，内容不能丢
    void reader.cancel()
    throw e
  }

  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let split = buffer.indexOf('\n\n')
    while (split >= 0) {
      dispatch(buffer.slice(0, split), handlers)
      buffer = buffer.slice(split + 2)
      split = buffer.indexOf('\n\n')
    }
  }
}

function dispatch(raw: string, handlers: Record<string, SseHandler>) {
  let event = 'message'
  const dataLines: string[] = []

  for (const line of raw.split('\n')) {
    if (line.startsWith(':')) continue // 注释行（心跳）
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }

  const handler = handlers[event]
  if (!handler || dataLines.length === 0) return
  try {
    handler(JSON.parse(dataLines.join('\n')))
  } catch {
    // 半截的 JSON 只能来自连接被切断，丢掉这一条比整条流崩掉好
  }
}
