import { ref } from 'vue'

/**
 * 拖放区：拖着文件经过时亮起来，松手把文件交出去——拖进来的是目录，就把里面认得出的书
 * 一并交出去（界面稿⓪屏「文件或整个目录都行」）。书库与导入两屏共用。
 *
 * 三处看不见的坑：
 *
 * 一、**指针滑过子元素时，拖放区会先收到一次 `dragleave`。** 只拿 dragover / dragleave
 * 拨一个布尔，指针从空白处滑到一张书卡上，遮罩就灭一下再亮，一路闪着过去。这里数进出的
 * 层数，回到 0 才算真的离开。
 *
 * 二、**拖进来的不一定是文件。** 页面里选中的一段字也拖得动；只认带着 `Files` 的那种，
 * 否则拖一段字过来也会亮起「松手就开始解析」。
 *
 * 三、**目录要在 `drop` 那一刻同步取出条目。** 事件一结束 `DataTransfer` 就清空了，
 * 等到下一个 await 再去拿，拿到的是空的。条目先取出来，里面的文件之后再慢慢读。
 */

/** 松手时交出的东西：要传的文件，以及拖进来却一本书都没挑出来的目录（要给人一句交代）。 */
export interface Dropped {
  files: File[]
  emptyFolders: string[]
}

export function useFileDrop(onDrop: (dropped: Dropped) => void, keep: (name: string) => boolean) {
  const dragging = ref(false)
  let depth = 0

  function enter(event: DragEvent) {
    if (!carriesFiles(event)) return
    event.preventDefault()
    depth += 1
    dragging.value = true
  }

  function over(event: DragEvent) {
    if (!carriesFiles(event)) return
    // 不拦下 dragover，浏览器就不认这里能放东西，松手时会直接去打开那个文件
    event.preventDefault()
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  }

  function leave(event: DragEvent) {
    if (!carriesFiles(event)) return
    depth = Math.max(0, depth - 1)
    if (depth === 0) dragging.value = false
  }

  function drop(event: DragEvent) {
    if (!carriesFiles(event) || !event.dataTransfer) return
    event.preventDefault()
    depth = 0
    dragging.value = false
    const { files, folders } = split(event.dataTransfer)
    void Promise.all(folders.map((folder) => booksIn(folder, keep))).then((found) => {
      onDrop({
        files: [...files, ...found.flat()],
        emptyFolders: folders.filter((_, i) => found[i].length === 0).map((f) => f.name)
      })
    })
  }

  return { dragging, listeners: { dragenter: enter, dragover: over, dragleave: leave, drop } }
}

function carriesFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files')
}

/** 同步把拖进来的东西分成文件与目录。浏览器不给条目接口时，只能照 `files` 全当文件。 */
function split(transfer: DataTransfer): { files: File[]; folders: FileSystemDirectoryEntry[] } {
  const files: File[] = []
  const folders: FileSystemDirectoryEntry[] = []
  for (const item of Array.from(transfer.items)) {
    if (item.kind !== 'file') continue
    const entry = item.webkitGetAsEntry?.()
    if (entry?.isDirectory) {
      folders.push(entry as FileSystemDirectoryEntry)
      continue
    }
    const file = item.getAsFile()
    if (file) files.push(file)
  }
  if (!files.length && !folders.length) files.push(...Array.from(transfer.files))
  return { files, folders }
}

/** 目录里（连同子目录）认得出的书。`.` 打头的不看：那多半是系统留下的。 */
async function booksIn(
  folder: FileSystemDirectoryEntry,
  keep: (name: string) => boolean
): Promise<File[]> {
  const out: File[] = []
  for (const entry of await entriesOf(folder)) {
    if (entry.name.startsWith('.')) continue
    if (entry.isDirectory) {
      out.push(...(await booksIn(entry as FileSystemDirectoryEntry, keep)))
    } else if (keep(entry.name)) {
      const file = await fileOf(entry as FileSystemFileEntry)
      if (file) out.push(file)
    }
  }
  return out
}

/** `readEntries` 一次只给一批（Chrome 一批一百个），要一直读到给空为止。 */
function entriesOf(folder: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
  const reader = folder.createReader()
  const all: FileSystemEntry[] = []
  return new Promise((resolve) => {
    const next = () =>
      reader.readEntries(
        (batch) => {
          if (!batch.length) {
            resolve(all)
            return
          }
          all.push(...batch)
          next()
        },
        () => resolve(all)
      )
    next()
  })
}

function fileOf(entry: FileSystemFileEntry): Promise<File | null> {
  return new Promise((resolve) => entry.file(resolve, () => resolve(null)))
}
