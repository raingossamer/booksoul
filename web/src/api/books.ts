import { request, requestBlob, requestUpload } from './client'
import type {
  Book,
  BookDetail,
  BookState,
  ChapterContent,
  Job,
  Note,
  Page,
  Report,
  UploadAccepted
} from '@/types'

export function listBooks(page = 1, size = 20): Promise<Page<Book>> {
  return request<Page<Book>>(`/api/books?page=${page}&size=${size}`)
}

/** 上传一本书。返回得快是因为解析在后台跑，拿 jobId 去问进度。 */
export function uploadBook(file: File): Promise<UploadAccepted> {
  const form = new FormData()
  form.append('file', file)
  return requestUpload<UploadAccepted>('/api/books', form)
}

/** 用当初那个原始文件重跑一遍，复用同一个 book_id 与目录。 */
export function reparseBook(bookId: number): Promise<UploadAccepted> {
  return request<UploadAccepted>(`/api/books/${bookId}/reparse`, { method: 'POST' })
}

export function getJob(jobId: number): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}`)
}

export function getBook(bookId: number): Promise<BookDetail> {
  return request<BookDetail>(`/api/books/${bookId}`)
}

/** 这本书此刻的状态：一条查询。解析中的轮询读它，不必每拍拉一整份详情。 */
export function getBookState(bookId: number): Promise<BookState> {
  return request<BookState>(`/api/books/${bookId}/state`)
}

export function getReport(bookId: number): Promise<Report> {
  return request<Report>(`/api/books/${bookId}/report`)
}

/** 把一条存疑标为已核对。核对过的不再打扰。 */
export function resolveNote(bookId: number, noteId: number, resolved = true): Promise<Note> {
  return request<Note>(`/api/books/${bookId}/notes/${noteId}`, {
    method: 'PATCH',
    body: { resolved }
  })
}

/** 一节正文。`idx` 是 1 起的阅读序号，不是 chapter.id —— 两者混了会读到错位的章节。 */
export function getChapter(bookId: number, idx: number): Promise<ChapterContent> {
  return request<ChapterContent>(`/api/books/${bookId}/chapters/${idx}`)
}

/** 正文里引的一张图（`![](images/<name>)`）。服务端只端位图，SVG 一律 404。 */
export function getBookImage(bookId: number, name: string): Promise<Blob> {
  return requestBlob(`/api/books/${bookId}/images/${encodeURIComponent(name)}`)
}
