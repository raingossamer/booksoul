import { ref } from 'vue'

import { ApiError } from '@/api/client'
import { deleteConversation, listConversations } from '@/api/conversations'
import type { Conversation } from '@/types'

/**
 * 一串会话：书库「最近问过的」、书籍详情「这本书的会话」共用。
 *
 * 删之前问不问，看有没有东西会丢：一轮都没问过的空会话（点了「开始对话」又走开
 * 留下的）直接删；问过的要说清几轮问答会跟着没、不能恢复。每次都弹的确认框等于
 * 没有确认框（`frontend.md`「凡是把用户的东西换掉的操作」）。
 */
export function useConversations(limit: number, bookId?: () => number) {
  const items = ref<Conversation[]>([])
  const removing = ref<number | null>(null)
  const error = ref('')

  async function load() {
    try {
      items.value = await listConversations(limit, bookId?.())
    } catch {
      // 列表读不出来不该拖垮这一屏：书还在，那才是主角
    }
  }

  async function remove(item: Conversation) {
    if (
      item.turnCount > 0 &&
      !window.confirm(`删掉「${item.title}」？${item.turnCount} 轮问答会一起删掉，不能恢复。`)
    ) {
      return
    }
    removing.value = item.id
    error.value = ''
    try {
      await deleteConversation(item.id)
      // 重拉而不是就地摘掉：列表有上限，删掉一条，后面那条要补上来
      await load()
    } catch (e) {
      // 正在回答时服务端回 409，那句话照样显示
      error.value = e instanceof ApiError ? e.message : '没能删掉，再试一次。'
    } finally {
      removing.value = null
    }
  }

  return { items, removing, error, load, remove }
}
