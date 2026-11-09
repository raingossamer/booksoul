/* 正文额度的来路，说给人听。
 *
 * ⑧屏那一格数字旁边的小字、以及换模型时窗口输入框旁的标注，都用这一份。
 * 各写一份的话，同一个来路会在两处叫两个名字，用户会以为是两件事。
 */
import type { ContextSource } from '@/types'

/** 挤在数字旁边的短名 */
export const SOURCE_TAG: Record<ContextSource, string> = {
  vendor: '供应商',
  manual: '你填的',
  builtin: '内置',
  estimate: '估计'
}

/** 悬停时的说法：这个数是怎么来的、该不该复核 */
export const SOURCE_HINT: Record<ContextSource, string> = {
  vendor: '供应商报的上下文窗口',
  manual: '你填的上下文窗口',
  builtin: '内置表里的上下文窗口',
  estimate: '估计的窗口，建议填上真实值'
}
