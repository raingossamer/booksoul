/* 键盘上几件浏览器不替你分清的事。
 *
 * 「回车就提交」的输入框都要先问一句：这个回车是不是输入法的？用中文输入法敲一个英文词
 * （模型名、一个术语），按回车是让字母上屏，不是提交。不分的话，问题被半截发出去、模型名
 * 半截存下来——而发出去的那一下已经在花钱。
 *
 * 要挂在 `keydown` 上判。输入法那次回车的 `keydown` 带着 `isComposing`（Safari 只给
 * `keyCode` 229）；到了 `keyup`，拼字已经结束，那个回车看上去和普通回车一模一样。
 */

/** 这个回车是输入法在用（选词、上屏），不是给页面的。 */
export function isImeEnter(event: KeyboardEvent): boolean {
  return event.isComposing || event.keyCode === 229
}
