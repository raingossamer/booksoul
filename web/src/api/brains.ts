/* 大脑。**这个文件里没有 key。**
 *
 * key 只存在 localStorage（见 `lib/brainKeys.ts`），提问时经 `X-Brain-Key`
 * 请求头带上（S7）。它不进请求体、不进查询串、不进任何这里的类型。
 */
import type { Brain, BrainCreate, ModelChoice, ModelList, Persona, PersonaKey } from '@/types'

import { request } from './client'

/** 带上 bookId 就多回答一句「这本书装得进它吗」。 */
export function listBrains(bookId?: number): Promise<Brain[]> {
  const query = bookId ? `?bookId=${bookId}` : ''
  return request<Brain[]>(`/api/brains${query}`)
}

/**
 * 用这台浏览器里存的 key 去问这家：你有哪些模型。
 *
 * 带 `brain` —— 这是除提问之外**唯一**一个带 `X-Brain-Key` 的请求：服务端要拿着
 * key 去问供应商。不带 key 的大脑（本机端点）照样能列。
 */
export function listModels(name: string): Promise<ModelList> {
  return request<ModelList>(`/api/brains/${encodeURIComponent(name)}/models`, { brain: name })
}

/** 换模型并存下来。只影响以后开的会话；返回换过之后的那一行。 */
export function saveModel(name: string, choice: ModelChoice): Promise<Brain> {
  return request<Brain>(`/api/brains/${encodeURIComponent(name)}/model`, {
    method: 'PUT',
    body: choice
  })
}

/** 恢复默认：模型、窗口、来路全部回到预设。 */
export function resetModel(name: string): Promise<void> {
  return request<void>(`/api/brains/${encodeURIComponent(name)}/model`, { method: 'DELETE' })
}

export function addBrain(payload: Partial<BrainCreate>): Promise<Brain> {
  return request<Brain>('/api/brains', { method: 'POST', body: payload })
}

export function deleteBrain(brainId: number): Promise<void> {
  return request<void>(`/api/brains/${brainId}`, { method: 'DELETE' })
}

/* 人格提示词（⑧屏右栏）。放在这个文件里是因为⑧屏就是一屏：
 * 左半接哪个模型、右半用什么口气读，两件事一起构成「怎么跟这本书说话」。
 *
 * **改动对所有用户生效**（Spec N5：本轮不建 user_personas 表）。
 * 这一点在类型里看不出来——没有 userId——所以界面上必须写明白。
 */

/** 三种人格的名录，不带正文。 */
export function listPersonas(): Promise<Persona[]> {
  return request<Persona[]>('/api/personas')
}

export function getPersona(key: PersonaKey): Promise<Persona> {
  return request<Persona>(`/api/personas/${key}`)
}

/** 覆写。占位符拼错了服务端会 422 拦下来，那句话直接给用户看。 */
export function savePersona(key: PersonaKey, text: string): Promise<Persona> {
  return request<Persona>(`/api/personas/${key}`, { method: 'PUT', body: { text } })
}

/** 还原成第一次从这里改动之前的那一版。 */
export function restorePersona(key: PersonaKey): Promise<Persona> {
  return request<Persona>(`/api/personas/${key}/restore`, { method: 'POST' })
}
