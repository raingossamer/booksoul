/* 模型 API key 在这台浏览器里的存放处。
 *
 * **这是 key 在整个系统里唯一被存下来的地方。** 服务器不存、不记、不回显；
 * 数据库里那张 brains 表连一列放它的地方都没有（`sql.md`「约定」）。
 *
 * 所以这个文件的职责很窄：读、写、忘掉、以及掩码显示。它刻意不提供
 * 「导出全部 key」这样的便利函数——那种函数的存在本身就是一个泄漏面。
 *
 * localStorage 按源隔离，同一台机器的不同浏览器互不可见。这既是它的安全性
 * 来源，也是它的代价：**换台机器就要重填一次**。设置页把这句话写明白了，
 * 而不是让用户自己发现。
 */

import type { Brain } from '@/types'

const KEY_PREFIX = 'booksoul.brainKey.'
const CHOSEN_BRAIN = 'booksoul.brain'
/** 没选过、或选的那个已经不在了，新开的会话用它 */
const DEFAULT_BRAIN = 'claude'

export function readBrainKey(name: string): string {
  try {
    return localStorage.getItem(KEY_PREFIX + name) ?? ''
  } catch {
    // 隐私模式、被清过的站点数据、禁写 storage 的浏览器都会走到这里。
    // 读不到就当没填过，不该让整个设置页崩掉。
    return ''
  }
}

export function saveBrainKey(name: string, key: string): void {
  try {
    const trimmed = key.trim()
    if (trimmed) localStorage.setItem(KEY_PREFIX + name, trimmed)
    else localStorage.removeItem(KEY_PREFIX + name)
  } catch {
    // 存不下就是这次会话内有效。比让「保存」这个动作整个炸掉好。
  }
}

export function forgetBrainKey(name: string): void {
  saveBrainKey(name, '')
}

/**
 * 这个大脑现在问得出去吗：本机的（ollama、LM Studio）不要 key，别的要这台浏览器里
 * 存着它的 key。
 *
 * 设置页「点选能用的就回书籍详情」与对话页「输入框开不开」读的是这同一条。
 * 原先对话页只看「存没存 key」：本机的大脑在设置页算能用、回到对话页却问不出去。
 */
export function isReady(brain: Pick<Brain, 'name' | 'needsKey'>): boolean {
  return !brain.needsKey || Boolean(readBrainKey(brain.name))
}

export function readChosenBrain(): string {
  try {
    return localStorage.getItem(CHOSEN_BRAIN) ?? ''
  } catch {
    return ''
  }
}

/**
 * 新开会话时用哪个大脑：选过、而且它还在，就是选的那个；否则是 claude，
 * 列表里连 claude 都没有就是列表里的第一个。
 *
 * 开会话（ChatView）、书籍详情「你选的是哪个」、设置页那个圆点读的必须是同一个——
 * 原先这里兜底是 claude、设置页另兜一份「列表第一个」，而选中的自定义端点被删掉之后
 * 谁都不核对它还在不在：设置页点着一个，书籍详情一个都不标，开会话报「没有这个大脑」。
 *
 * `known` 是手里那份大脑名单；还没拉到（或拉不到）就不核对，让服务端去说。
 */
export function brainForNewChat(known: readonly string[] = []): string {
  const remembered = readChosenBrain()
  if (!known.length) return remembered || DEFAULT_BRAIN
  if (known.includes(remembered)) return remembered
  return known.includes(DEFAULT_BRAIN) ? DEFAULT_BRAIN : known[0]
}

/** 记下选了谁；给空串就是忘掉这个选择（选的那个被删掉了）。 */
export function saveChosenBrain(name: string): void {
  try {
    if (name) localStorage.setItem(CHOSEN_BRAIN, name)
    else localStorage.removeItem(CHOSEN_BRAIN)
  } catch {
    // 同上：记不住选择，下次回到默认，不影响这次用
  }
}

/**
 * 保存后回显用的掩码。
 *
 * 留出末四位而不是全遮：手里有两个 key 的时候，全遮的两行长得一模一样，
 * 没法确认自己填的是哪一个。末四位够分辨，不够使用——各家控制台都是这么做的。
 * 短到看不出是不是完整 key 的，一位都不留。
 */
export function maskKey(key: string): string {
  if (!key) return ''
  if (key.length <= 12) return '·'.repeat(8)
  return `${'·'.repeat(8)}${key.slice(-4)}`
}
