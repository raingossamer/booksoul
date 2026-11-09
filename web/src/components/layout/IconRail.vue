<script setup lang="ts">
/* 72px 深色图标栏，九屏共用。
 *
 * 还没有对应路由的几项**不渲染成按钮**——界面稿里它们本来也是暗色的非激活态。
 * 做成点了没反应的按钮比暗着更糟。每片做完就把对应的那一格接上去。
 *
 * 「阅读」那一格是个例外：它永远是「某本书的某一节」，没有一个不带书的入口，
 * 所以它不做链接，只在你正在读的时候亮起来指示位置。
 */
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()
const reading = computed(() => route.name === 'book-read')
// 对话和阅读一样，永远是「某本书的某一次对话」，没有不带书的入口
const chatting = computed(() => route.name === 'chat' || route.name === 'chat-new')
</script>

<template>
  <nav class="rail" aria-label="主导航">
    <RouterLink to="/" class="brand" aria-label="书魂">魂</RouterLink>

    <RouterLink to="/" class="slot" active-class="slot--active" aria-label="书库" title="书库">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M4 5h5v14H4z" />
        <path d="M11 5h4v14h-4z" />
        <path d="M17.5 5.6l3.2 1 -3.6 12.8 -3.2-1z" />
      </svg>
    </RouterLink>

    <RouterLink
      to="/import"
      class="slot"
      active-class="slot--active"
      aria-label="导入与解析"
      title="导入与解析"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M12 3v11" />
        <path d="M8 10l4 4 4-4" />
        <path d="M4 18v2h16v-2" />
      </svg>
    </RouterLink>
    <span
      class="slot"
      :class="{ 'slot--active': reading }"
      :aria-label="reading ? '阅读' : undefined"
      :aria-hidden="reading ? undefined : 'true'"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M12 6.5C10.5 5 8 4.5 4 5v13c4-.5 6.5 0 8 1.5" />
        <path d="M12 6.5C13.5 5 16 4.5 20 5v13c-4-.5-6.5 0-8 1.5" />
        <path d="M12 6.5v13" />
      </svg>
    </span>
    <span
      class="slot"
      :class="{ 'slot--active': chatting }"
      :aria-label="chatting ? '对话' : undefined"
      :aria-hidden="chatting ? undefined : 'true'"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M20 12a7 7 0 0 1-9.8 6.4L5 20l1.6-4.8A7 7 0 1 1 20 12z" />
      </svg>
    </span>

    <span class="spacer" />

    <RouterLink
      to="/settings"
      class="slot"
      active-class="slot--active"
      aria-label="设置"
      title="大脑与人格"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round">
        <path d="M4 7h10" />
        <path d="M18 7h2" />
        <path d="M4 12h3" />
        <path d="M11 12h9" />
        <path d="M4 17h9" />
        <path d="M17 17h3" />
        <circle cx="16" cy="7" r="2" />
        <circle cx="9" cy="12" r="2" />
        <circle cx="15" cy="17" r="2" />
      </svg>
    </RouterLink>
  </nav>
</template>

<style scoped>
.rail {
  width: var(--rail-width);
  flex-shrink: 0;
  background: var(--rail);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px 0;
  gap: 4px;
}

.brand {
  width: 30px;
  height: 30px;
  margin-bottom: 22px;
  border: 1.5px solid var(--mark);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 16px;
  color: var(--ink-text);
  text-decoration: none;
}

.slot {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-left: 2px solid transparent;
}

.slot svg {
  width: 20px;
  height: 20px;
  stroke: var(--rail-icon);
}

/* 能点的那几格悬停时亮一档：深底上的暗色图标，不亮一下看不出哪几格是入口 */
a.slot:hover svg {
  stroke: var(--rail-icon-active);
}

.slot--active {
  background: var(--rail-active);
  border-left-color: var(--mark);
}

.slot--active svg {
  stroke: var(--rail-icon-active);
}

.spacer {
  flex-grow: 1;
}
</style>
