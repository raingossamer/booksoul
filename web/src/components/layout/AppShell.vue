<script setup lang="ts">
/* 图标栏 + 内容区的整体框架，九屏共用。
 *
 * 标题行的 title / hint 两段来自界面稿：书名用衬线，右边那个小小的等宽字
 * 是状态（空 / 16 节 / 解析中），刻意不着色——成功是默认态。
 */
import IconRail from './IconRail.vue'

defineProps<{ title: string; hint?: string }>()
</script>

<template>
  <div class="shell">
    <IconRail />
    <main class="body">
      <header class="head">
        <h1>{{ title }}</h1>
        <span v-if="hint" class="mono hint">{{ hint }}</span>
        <span class="grow" />
        <slot name="head-right" />
      </header>

      <slot />

      <footer v-if="$slots.foot" class="foot">
        <slot name="foot" />
      </footer>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  background: var(--paper);
  overflow: hidden;
}

.body {
  flex-grow: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 26px 40px 20px;
  border-bottom: 1px solid var(--rule);
}

.head h1 {
  font-size: 22px;
}

.hint {
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.06em;
}

.grow {
  flex-grow: 1;
}

.foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 40px;
  border-top: 1px solid var(--rule);
  background: var(--panel);
}
</style>
