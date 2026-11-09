<script setup lang="ts">
/* 一串会话，点进去接着聊，旁边能删（书库「最近问过的」、书籍详情「这本书的会话」）。
 *
 * 展示组件：不发请求。删之前要不要问、删完怎么补，是 `useConversations` 的事。
 */
import type { Conversation, PersonaKey } from '@/types'

defineProps<{
  items: Conversation[]
  /** 书库里跨着书列，要写是哪本；书籍详情里都是这一本，不写 */
  showBook?: boolean
  /** 窄栏里一条一行（书籍详情侧栏）；宽处铺成网格（书库） */
  stacked?: boolean
  /** 正在删的那一条 */
  removing?: number | null
}>()

defineEmits<{ remove: [item: Conversation] }>()

const PERSONA: Record<PersonaKey, string> = {
  scholar: '研读者',
  author: '作者',
  book: '书'
}
</script>

<template>
  <div class="list" :class="{ 'list--stacked': stacked }">
    <div v-for="item in items" :key="item.id" class="item">
      <RouterLink class="go" :to="`/chat/${item.id}`">
        <span class="q">{{ item.title || '（还没问出第一个问题）' }}</span>
        <span class="mono meta">
          <template v-if="showBook">{{ item.bookTitle }} · </template>
          {{ PERSONA[item.persona] }} · {{ item.turnCount }} 轮
        </span>
      </RouterLink>
      <button
        class="drop"
        type="button"
        :disabled="removing === item.id"
        :aria-label="`删掉会话「${item.title || '还没问出第一个问题'}」`"
        @click="$emit('remove', item)"
      >
        {{ removing === item.id ? '删除中' : '删掉' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 8px;
}

.list--stacked {
  grid-template-columns: 1fr;
  gap: 6px;
}

.item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding-right: 12px;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.item:hover {
  border-color: var(--mark);
}

.go {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 10px 0 10px 13px;
  text-decoration: none;
}

.list--stacked .go {
  padding: 8px 0 8px 11px;
}

/* 朱批 = 人问的那句话 */
.q {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item:hover .q {
  color: var(--mark);
}

.meta {
  font-size: 10px;
  color: var(--text-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 与⑧屏「删掉」同一种写法：一个安静的文字按钮，悬停才着朱批 */
.drop {
  flex-shrink: 0;
  padding: 0;
  background: none;
  border: none;
  font-size: 11px;
  color: var(--text-faint);
  white-space: nowrap;
}

.drop:hover:not(:disabled) {
  color: var(--mark);
}

.drop:focus-visible {
  outline: 1px solid var(--mark);
  outline-offset: 2px;
}
</style>
