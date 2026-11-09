/* markdown-it-footnote 4.x 不带类型声明，这里补一个最小的。
 * 只声明我们真正用到的那一件事：它是个 markdown-it 插件。 */
declare module 'markdown-it-footnote' {
  import type { PluginSimple } from 'markdown-it'

  const footnotePlugin: PluginSimple
  export default footnotePlugin
}
