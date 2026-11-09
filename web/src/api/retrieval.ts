/* 选章明细（界面稿⑥屏）。
 *
 * 挂在会话上而不是某条回答上：一轮里三种人格各答一次，却只选一次章。
 * 拿到的是这个会话**最近一次真正做过的**选章决策 —— 后续轮次复用它，
 * 它们没有自己的决策可言。
 */
import type { Retrieval } from '@/types'

import { request } from './client'

export function getRetrieval(conversationId: number): Promise<Retrieval> {
  return request<Retrieval>(`/api/conversations/${conversationId}/retrieval`)
}
