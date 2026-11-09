import { computed, onUnmounted, ref } from 'vue'

import { listBooks, reparseBook, uploadBook } from '@/api/books'
import { ApiError } from '@/api/client'
import type { Book } from '@/types'
import { useFormats } from './useFormats'

/** 还在解析时每隔多久回来看一眼。S3 换成 SSE 之后这条轮询就退成兜底。 */
const POLL_MS = 1500

export function useLibrary() {
  const books = ref<Book[]>([])
  const total = ref(0)
  const loading = ref(true)
  const error = ref('')
  /**
   * 上传这一步本身失败的文件（体积超限、空文件）。
   * 解析失败的书在书库里有自己的卡片，不进这里——它已经是一本书了。
   */
  const rejected = ref<{ name: string; reason: string }[]>([])
  /**
   * 正在往上传的文件。一本几十 MB 的书要传好几秒，这期间书库里还没有它的卡片——
   * 什么都不显示，拖进来的人只会以为没拖上，再拖一次。
   */
  const uploading = ref<{ key: number; name: string }[]>([])
  let uploads = 0

  const formats = useFormats()
  const parsing = computed(() => books.value.some((b) => b.status === 'parsing'))
  let timer: ReturnType<typeof setTimeout> | null = null

  function schedule() {
    if (timer) clearTimeout(timer)
    timer = null
    // 只在有书正在解析时才回来看。没有在途的解析就不打扰服务器。
    // 刷新页面也照样接得上——判据是书库里的状态，不是这次会话记住的 jobId。
    if (parsing.value) timer = setTimeout(refresh, POLL_MS)
  }

  async function refresh() {
    try {
      const page = await listBooks()
      books.value = page.items
      total.value = page.total
      error.value = ''
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : '书库读不出来。'
    } finally {
      loading.value = false
      schedule()
    }
  }

  async function add(files: FileList | File[]) {
    await formats.load()
    // 逐个传，各走各的请求：一个失败不影响其余，这是验收标准里明写的。
    for (const file of Array.from(files)) {
      // 上传前粗筛，判据来自 /api/formats，不在前端写死后缀
      const reason = formats.reject(file)
      if (reason) {
        rejected.value.push({ name: file.name, reason })
        continue
      }
      const key = uploads++
      uploading.value = [...uploading.value, { key, name: file.name }]
      try {
        await uploadBook(file)
        // 传上去一本就刷一次：卡片一本一本出来，不等整批传完才一起冒出来
        await refresh()
      } catch (e) {
        rejected.value.push({
          name: file.name,
          reason: e instanceof ApiError ? e.message : '没能传上去。'
        })
      } finally {
        uploading.value = uploading.value.filter((one) => one.key !== key)
      }
    }
  }

  /** 拖进来一个目录，里面却一本认得出的书都没有：说一声，不然像是没拖上。 */
  function skipFolders(names: string[]) {
    for (const name of names) {
      rejected.value.push({ name, reason: '这个目录里没有认得出的书。' })
    }
  }

  /** 重新解析一本停下来的书。解析在后台跑，轮询会自己接上。 */
  async function reparse(bookId: number) {
    try {
      await reparseBook(bookId)
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : '重新解析没能开始。'
    }
    await refresh()
  }

  function dismiss(name: string) {
    rejected.value = rejected.value.filter((item) => item.name !== name)
  }

  onUnmounted(() => {
    if (timer) clearTimeout(timer)
  })

  return {
    books,
    total,
    loading,
    error,
    rejected,
    uploading,
    formats,
    refresh,
    add,
    skipFolders,
    reparse,
    dismiss
  }
}
