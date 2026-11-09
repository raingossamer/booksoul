<script setup lang="ts">
/**
 * 返回上一层。
 *
 * 做成按钮而不是一行带下划线的小字：它往往是这一屏唯一的出口，而 12px 的次要
 * 文字色贴在标题旁边几乎读不到。找不到路的人会去按浏览器后退——那会把他退出
 * 整个应用，而不是退回上一层。
 *
 * 用**描边**的朱批而不是实心：实心朱批是主按钮（「加书」「选择文件」那种发起
 * 动作的）。返回不是一个动作，是导航；描边够醒目，又不跟主按钮抢位置。
 * 这条与 frontend.md「样式约定」那句「区分平级的身份用填法，不借用状态色」同源。
 */
defineProps<{
  /** 目标路由 */
  to: string
  /** 按钮上的字，如「回书库」 */
  label: string
}>()
</script>

<template>
  <RouterLink class="back" :to="to">
    <span class="arrow" aria-hidden="true">←</span>
    {{ label }}
  </RouterLink>
</template>

<style scoped>
.back {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-left: 14px;
  height: 34px;
  padding: 0 16px;
  border: 1px solid var(--mark);
  border-radius: var(--radius);
  color: var(--mark);
  font-family: var(--font-sans);
  font-size: 14px;
  font-weight: 500;
  text-decoration: none;
  white-space: nowrap;
}

.back:hover,
.back:focus-visible {
  background: var(--mark);
  color: var(--surface);
}

.arrow {
  font-size: 15px;
  line-height: 1;
}
</style>
