/* 对话。
 *
 * 提问那一条不在这里：它是 SSE，走 `composables/useAsk.ts`。放在一起会让人
 * 以为它和别的请求一样是「发出去、拿回来」——它是一条会开着几十秒的流。
 * 开会话也不在这里：会话从第一问开始存在，开会话回的就是第一问的流，同样走 `useAsk`。
 */
import type { Conversation, PersonaKey } from '@/types'

import { request } from './client'

/** 最近问过的，按最后一次提问排。给了 `bookId` 就只列这本书的。 */
export function listConversations(limit = 20, bookId?: number): Promise<Conversation[]> {
  const byBook = bookId === undefined ? '' : `&bookId=${bookId}`
  return request<Conversation[]>(`/api/conversations?limit=${limit}${byBook}`)
}

/** 连同问答、引文一起删，不能恢复。正在回答时服务端回 409。 */
export function deleteConversation(id: number): Promise<void> {
  return request<void>(`/api/conversations/${id}`, { method: 'DELETE' })
}

export function getConversation(id: number): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${id}`)
}

/** 「跟这个继续聊」：对读完挑一路接着往下。旁支一个字都不删。 */
export function pickPersona(id: number, persona: PersonaKey): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${id}`, {
    method: 'PATCH',
    body: { persona }
  })
}

/**
 * 处境模式：把「读者带来的是他自己的处境」那一段附到人格提示词后面。
 *
 * **拨它不花额外的钱。** 缓存断点在全书之后、人格之前，这一段只动断点之后
 * 那一块，整本书的缓存照常命中——所以它是一个可以随手拨的开关，不像「重新
 * 挑章节」那样要先问用户一句。
 */
export function setReflection(id: number, on: boolean): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${id}`, {
    method: 'PATCH',
    body: { reflection: on }
  })
}
