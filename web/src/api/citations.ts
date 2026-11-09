/* 溯源（界面稿⑤屏上半、⑦屏答栏）。 */
import type { Citation } from '@/types'

import { request } from './client'

/** 这条回答引了哪些原文。没引原文是空数组，不是错误。 */
export function listCitations(messageId: number): Promise<Citation[]> {
  return request<Citation[]>(`/api/messages/${messageId}/citations`)
}

/** 一条引文的来路。阅读页靠它画那条「从对话跳来」的横条。 */
export function getCitation(id: number): Promise<Citation> {
  return request<Citation>(`/api/citations/${id}`)
}
