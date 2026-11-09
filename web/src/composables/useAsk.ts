/* 问一轮，逐字接住（界面稿⑦屏）。
 *
 * 一轮可以有**不止一路**：对读同时跑三种人格。所以这里的状态一律按人格分开存，
 * 单人格只是「只有一路」的那个情形——两套状态迟早会在断流那条路上分叉。
 *
 * 三条纪律，都是为了同一件事——**用户花的钱不能白花**：
 *
 * 一、**流断了不重发。** 网络抖一下就自动重试，看起来很贴心，实际是让同一个
 * 问题被问两遍、计两次费（对读是六次）。这里断了就停下，把已经收到的那一段
 * 留在屏幕上，让用户自己决定要不要再花一次钱（Spec #38、#39）。
 *
 * 二、**已经收到的字不丢。** 出错时不清空 `streaming`，它会作为一条残缺回答
 * 留在那一轮里。服务端那边同样会把它落库（`StreamRecorder`），两边说的是同一件事。
 *
 * 三、**重新挑章节要用户点头。** `reselect` 默认 false。重选会换掉整套章节、
 * 缓存作废，这一轮的输入要按全价重算一次。
 *
 * 事件分两层，对应服务端那两层：`answered` / `failed` 是**某一路**的结局，
 * 收到就把那一列的转圈收掉；`done` / `error` 是**整轮**的结局，那时内容已经落库，
 * 可以放心回头去拉一次会话。
 *
 * 另有 `notice`：这一轮有什么不一样（「这本书重新解析过了，这次按全价算」）。
 * 它不跟着 token / done 清掉——那句话说的是这一整轮，说完了也还成立；
 * 下一次提问才清。
 *
 * 新会话的第一问也走这里：会话从第一问开始存在，开会话（`POST /api/conversations`）
 * 回的就是第一问的流，事件与提问那条一模一样。
 */
import { ref } from 'vue'

import { ApiError } from '@/api/client'
import { readSSE } from '@/composables/useSSE'
import type { PersonaKey, TurnNotice } from '@/types'

/** 这一问问到哪里：一个已有的会话，或者一个随这一问才建起来的会话。 */
export type AskTarget =
  | { conversationId: number }
  | {
      start: { bookId: number; persona: PersonaKey; brainName: string; reflection: boolean }
    }

export interface AskOptions {
  brain: string
  /** 这一轮要显示哪几列。对读三列，单人格一列。 */
  personas: PersonaKey[]
  /** 这一问用哪一种人格；和会话的不同，服务端就在写下这一问时换掉主干。对读时不给。 */
  persona?: PersonaKey
  reselect?: boolean
  compare?: boolean
  /**
   * 服务端接下了这一问：流开了，字还没来。新会话到这一刻才有 id——调用方拿它
   * 把地址换过去，流还在走时刷新页面，回到的就是这个会话，而不是一张新稿纸
   * （再问一遍就是第二个会话、第二笔钱）。
   */
  onAccepted?: (conversationId: number) => void
}

/** 开会话回 201，新会话在 `Location: /api/conversations/{id}`。 */
function createdId(response: Response): number {
  const id = Number(response.headers.get('Location')?.split('/').pop())
  if (!Number.isInteger(id) || id <= 0) {
    throw new ApiError(
      response.status,
      '会话已经建好，但这一页没能接上它。回书籍详情，从「这本书的会话」里打开它。'
    )
  }
  return id
}

type ByPersona<T> = Partial<Record<PersonaKey, T>>

export function useAsk() {
  /** 每一路正在流的那段文字。一边收一边显示，用户不必盯着空白等几十秒。 */
  const streaming = ref<ByPersona<string>>({})
  /** 这一路已经说完了（转圈可以收掉）。 */
  const settled = ref<ByPersona<boolean>>({})
  /** 这一路自己的错误。一列失败不该让另外两列跟着变白。 */
  const failures = ref<ByPersona<string>>({})
  /** 这一轮在显示哪几列。第一个 token 还没来时就要有，否则列是空的。 */
  const columns = ref<PersonaKey[]>([])
  /** 第一个字之前那句话：「正在挑章节…」。收到字就清掉。 */
  const stage = ref('')
  /** 这一轮的告示。收到字、说完了都不清，下一次提问才清。 */
  const notices = ref<TurnNotice[]>([])
  const asking = ref(false)
  /** 整轮的错误。某一路自己的错在 `failures` 里。 */
  const error = ref('')
  /**
   * 问到了这次没载入的章节，服务端把这一问拦下来了（还没花钱）。
   *
   * 和 `error` 分开：`error` 是「出事了」，这个是「要你做个决定」——
   * 重选会换掉整套章节、缓存作废，这一轮的输入按全价重算。界面上前者是
   * 一句说明，后者要给按钮。
   */
  const blocked = ref<{ message: string; question: string } | null>(null)
  let controller: AbortController | null = null

  async function ask(target: AskTarget, question: string, options: AskOptions) {
    const known = 'conversationId' in target ? target.conversationId : null
    const compare = options.compare ?? false
    const path = known === null ? '/api/conversations' : `/api/conversations/${known}/ask`
    const body =
      'start' in target
        ? { ...target.start, question, compare }
        : {
            question,
            persona: options.persona ?? null,
            reselect: options.reselect ?? false,
            compare
          }

    columns.value = [...options.personas]
    streaming.value = {}
    settled.value = {}
    failures.value = {}
    stage.value = ''
    notices.value = []
    error.value = ''
    blocked.value = null
    asking.value = true
    controller = new AbortController()

    const who = (data: Record<string, unknown>) => String(data.persona ?? '') as PersonaKey

    try {
      await readSSE(
        path,
        {
          stage: (data) => {
            stage.value = String(data.text ?? '')
          },
          notice: (data) => {
            notices.value = [
              ...notices.value,
              { code: String(data.code ?? ''), text: String(data.text ?? '') }
            ]
          },
          token: (data) => {
            // 开口了就把状态那行收掉：它说的是「还没开口」这件事
            stage.value = ''
            const persona = who(data)
            streaming.value = {
              ...streaming.value,
              [persona]: (streaming.value[persona] ?? '') + String(data.text ?? '')
            }
          },
          answered: (data) => {
            settled.value = { ...settled.value, [who(data)]: true }
          },
          failed: (data) => {
            const persona = who(data)
            settled.value = { ...settled.value, [persona]: true }
            failures.value = {
              ...failures.value,
              [persona]: String(data.message ?? '这一路没说完。')
            }
          },
          done: () => {
            stage.value = ''
          },
          error: (data) => {
            stage.value = ''
            error.value = String(data.message ?? '这条回答没说完。')
          }
        },
        {
          signal: controller.signal,
          method: 'POST',
          body,
          brain: options.brain,
          onOpen: (response) => options.onAccepted?.(known ?? createdId(response))
        }
      )
    } catch (e) {
      stage.value = ''
      if (e instanceof ApiError && e.code === 'reselect_required') {
        // 这一问根本没发出去，一个 token 都没花。把问题留着，用户点头就原样再发。
        blocked.value = { message: e.message, question }
      } else {
        // 不自动重试。重试就是让同一个问题再计一次费。
        error.value =
          e instanceof ApiError ? e.message : '这条回答的连接断了。已经说出来的部分留在下面。'
      }
    } finally {
      asking.value = false
      controller = null
    }
  }

  function stop() {
    controller?.abort()
  }

  return {
    streaming,
    settled,
    failures,
    columns,
    stage,
    notices,
    asking,
    error,
    blocked,
    ask,
    stop
  }
}
