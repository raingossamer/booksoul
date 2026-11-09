import { onMounted, onUnmounted, reactive, ref } from 'vue'

import { getJob, uploadBook } from '@/api/books'
import { ApiError } from '@/api/client'
import type { Job, ParseStage } from '@/types'
import { readSSE } from './useSSE'
import { useFormats } from './useFormats'

export type ImportState = 'rejected' | 'uploading' | 'parsing' | 'ready' | 'failed'

export interface ImportItem {
  key: string
  filename: string
  state: ImportState
  bookId?: number
  jobId?: number
  stage: ParseStage
  done: number
  total: number
  detectedFormat: string
  reason: string
  errorCode: string
  remedy: string
  stats: Record<string, number>
}

/** 进度推送断了之后，隔多久去问一次那条记录。 */
const POLL_MS = 1500

let counter = 0

/**
 * 这一批放在模块里，不放在页面里：离开这一页去书库看一眼再回来，一行行进度、停下来那几本
 * 的补救办法都还在。原先放在页面里，一走就清空，回来是一张「等着收书」的白纸——
 * 解析明明还在服务端跑，停下来的那本也还等着人照补救办法去做。
 */
const items = ref<ImportItem[]>([])
/** 正在看着的进度（按行）。离开这一页全部放掉，回来再接上。 */
const watching = new Map<string, AbortController>()
let onScreen = false

/**
 * ①屏：一批文件各自独立的导入进度。
 *
 * 每个文件一条 SSE 流。一次拖 5 个进来就是 5 条流、5 行进度，互不相干——
 * 「一次失败不毁掉整批」在这个形状下是天然成立的，不需要额外兜。
 */
export function useImports() {
  const formats = useFormats()

  function add(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      const item = reactive<ImportItem>({
        key: `${Date.now()}-${counter++}`,
        filename: file.name,
        state: 'uploading',
        stage: 'read',
        done: 0,
        total: 0,
        detectedFormat: '',
        reason: '',
        errorCode: '',
        remedy: '',
        stats: {}
      })
      items.value.unshift(item)

      // 上传前粗筛，别让注定失败的文件白传一趟。判据来自 /api/formats。
      const reason = formats.reject(file)
      if (reason) {
        item.state = 'rejected'
        item.reason = reason
        continue
      }

      void start(item, file)
    }
  }

  /** 拖进来一个目录，里面却一本认得出的书都没有：记一行，不然像是没拖上。 */
  function skipFolders(names: string[]) {
    for (const name of names) {
      items.value.unshift(
        reactive<ImportItem>({
          key: `${Date.now()}-${counter++}`,
          filename: name,
          state: 'rejected',
          stage: 'read',
          done: 0,
          total: 0,
          detectedFormat: '',
          reason: '这个目录里没有认得出的书。',
          errorCode: '',
          remedy: '',
          stats: {}
        })
      )
    }
  }

  async function start(item: ImportItem, file: File) {
    try {
      const accepted = await uploadBook(file)
      item.bookId = accepted.bookId
      item.jobId = accepted.jobId
      item.state = 'parsing'
      follow(item)
    } catch (e) {
      item.state = 'failed'
      item.reason = e instanceof ApiError ? e.message : '没能传上去。'
    }
  }

  /** 开始看这一行的进度。页面不在、已经在看、已经有结局的，都不必。 */
  function follow(item: ImportItem) {
    if (!onScreen || item.state !== 'parsing' || watching.has(item.key)) return
    const controller = new AbortController()
    watching.set(item.key, controller)
    void track(item, controller.signal).finally(() => {
      if (watching.get(item.key) === controller) watching.delete(item.key)
    })
  }

  async function track(item: ImportItem, signal: AbortSignal) {
    try {
      await readSSE(
        `/api/jobs/${item.jobId}/events`,
        {
          stage: (data) => applyJob(item, data as unknown as Job),
          done: (data) => {
            applyJob(item, data as unknown as Job)
            item.state = 'ready'
          },
          error: (data) => {
            // 带着错误码的才是「解析停下了」；不带的是推送自己出了事（「进度推送中断了」），
            // 解析多半还在跑——不能把一本正在解析的书标成停下了
            if (!data.errorCode) return
            item.state = 'failed'
            item.reason = String(data.detail ?? '解析没能完成。')
            item.errorCode = String(data.errorCode)
            item.remedy = String(data.remedy ?? '')
          }
        },
        { signal }
      )
    } catch {
      if (signal.aborted) return
    }

    // 推送断了、或者没说结局就结束了：不等于解析失败，它多半还在服务端跑着。
    // 改成隔一会儿问一次同一条记录（推送读的也是它），直到有结局
    while (item.state === 'parsing' && !signal.aborted) {
      await pause(POLL_MS, signal)
      if (signal.aborted) return
      try {
        settle(item, await getJob(item.jobId as number))
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          item.state = 'failed'
          item.reason = e.message
        }
        // 别的失败（网络抖了一下）下一拍再问
      }
    }
  }

  function applyJob(item: ImportItem, job: Job) {
    item.stage = job.stage
    item.done = job.progressDone
    item.total = job.progressTotal
    item.detectedFormat = job.detectedFormat
    item.stats = (job.cleanupStats ?? {}) as Record<string, number>
  }

  /** 问回来的那条记录：进度照抄，有了结局就落定。 */
  function settle(item: ImportItem, job: Job) {
    applyJob(item, job)
    if (!job.finishedAt) return
    if (job.errorCode) {
      item.state = 'failed'
      item.reason = job.errorMessage || '解析没能完成。'
      item.errorCode = job.errorCode
      item.remedy = job.remedy
    } else {
      item.state = 'ready'
    }
  }

  function forget(key: string) {
    watching.get(key)?.abort()
    items.value = items.value.filter((item) => item.key !== key)
  }

  onMounted(() => {
    onScreen = true
    // 回到这一页：还在解析的那几行接着看（走开时传完的，也从这里开始看）
    for (const item of items.value) follow(item)
  })

  onUnmounted(() => {
    // 离开页面就别再占着连接。解析本身在服务端照常跑完，回来时再接上
    onScreen = false
    for (const controller of watching.values()) controller.abort()
    watching.clear()
  })

  return { items, add, skipFolders, forget, formats }
}

/** 等一会儿；页面走了就不等了。 */
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        resolve()
      },
      { once: true }
    )
  })
}
