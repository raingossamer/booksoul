<script setup lang="ts">
/* 书库（界面稿③屏）。书库为空时渲染首次启动态（⓪屏），不单独占路由。
 *
 * 拖放接的是整个内容区而不只是那个虚线框：书库里已经有书的时候，⓪屏那个框
 * 不在页面上，但「把书拖进来」这个动作应该照样成立。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import BookCard from '@/components/book/BookCard.vue'
import EmptyLibrary from '@/components/book/EmptyLibrary.vue'
import ConversationList from '@/components/chat/ConversationList.vue'
import AppShell from '@/components/layout/AppShell.vue'
import { useConversations } from '@/composables/useConversations'
import { useFileDrop } from '@/composables/useFileDrop'
import { useLibrary } from '@/composables/useLibrary'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const {
  books,
  total,
  loading,
  error,
  rejected,
  uploading,
  formats,
  refresh,
  add,
  skipFolders,
  reparse,
  dismiss
} = useLibrary()

const {
  items: recent,
  removing,
  error: removeError,
  load: loadRecent,
  remove: removeRecent
} = useConversations(6)

const picker = ref<HTMLInputElement | null>(null)
const { dragging, listeners: drop } = useFileDrop(({ files, emptyFolders }) => {
  skipFolders(emptyFolders)
  if (files.length) void add(files)
}, formats.recognizes)

const hint = computed(() => {
  if (loading.value) return '读取中'
  if (total.value === 0) return '空'
  const words = books.value.reduce((sum, b) => sum + b.wordCount, 0)
  return `${total.value} 本 · ${words.toLocaleString()} 字`
})

function openPicker() {
  picker.value?.click()
}

async function onPicked(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) await add(input.files)
  // 清空，否则连着选同一个文件两次不会再触发 change
  input.value = ''
}

/**
 * 退出就回登录页。只清掉登录态、人留在原地的话，这一页还摆着上一个人的书，
 * 而此后每个请求都是 401——不在登录状态，401 处理也不会再送他走。
 */
function signOut() {
  auth.logout()
  void router.replace({ name: 'login' })
}

onMounted(() => {
  void formats.load()
  void refresh()
  void loadRecent()
})
</script>

<template>
  <AppShell title="书库" :hint="hint">
    <template #head-right>
      <span v-if="auth.user" class="who">{{ auth.user.displayName || auth.user.email }}</span>
      <button v-if="total > 0" class="add" type="button" @click="openPicker">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">
          <path d="M12 5v14" />
          <path d="M5 12h14" />
        </svg>
        加书
      </button>
      <button class="signout" type="button" @click="signOut">退出</button>
    </template>

    <input
      ref="picker"
      class="picker"
      type="file"
      multiple
      :accept="formats.accept.value"
      @change="onPicked"
    />

    <div class="drop-target" :class="{ 'drop-target--over': dragging }" v-on="drop">
      <p v-if="error" class="banner banner--doubt" role="alert">{{ error }}</p>

      <!-- 靛 = 机器在做事。传完一本，它的卡片就出现在下面 -->
      <p v-if="uploading.length" class="banner banner--machine" role="status">
        正在上传 {{ uploading.map((one) => one.name).join('、') }}
      </p>

      <p v-for="item in rejected" :key="item.name" class="banner banner--doubt" role="alert">
        <span>{{ item.name }}：{{ item.reason }}</span>
        <button type="button" class="dismiss" @click="dismiss(item.name)">知道了</button>
      </p>

      <EmptyLibrary
        v-if="!loading && total === 0"
        :formats="formats.available.value.map((f) => f.label)"
        @pick="openPicker"
      />

      <div v-else-if="!loading" class="shelf">
        <BookCard v-for="book in books" :key="book.id" :book="book" @reparse="reparse" />
      </div>

      <section v-if="recent.length" class="recent">
        <span class="mono cap">最近问过的</span>
        <p v-if="removeError" class="recent-error" role="alert">{{ removeError }}</p>
        <ConversationList :items="recent" :removing="removing" show-book @remove="removeRecent" />
      </section>

      <div v-if="dragging" class="curtain">
        <span>松手就开始解析</span>
      </div>
    </div>
  </AppShell>
</template>

<style scoped>
.recent {
  display: flex;
  flex-direction: column;
  gap: 9px;
  /* 左右必须和 .shelf 的 padding 同宽：不给内边距的话，这一段连同上面那条
     分割线会一路顶到内容区最左边，比上面的书卡片左移 36px —— 看起来像是
     两块不同的东西各自为政。 */
  margin: 30px 36px 30px;
  padding-top: 20px;
  border-top: 1px solid var(--rule);
}

/* 删不掉（多半是那个会话还在回答）。黄檗 = 等一等再说，不是坏了 */
.recent-error {
  margin: 0;
  padding: 8px 12px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  font-size: 12px;
  color: var(--doubt);
}

.cap {
  font-size: 10px;
  letter-spacing: 0.16em;
  color: var(--text-faint);
}

.who {
  font-size: 12px;
  color: var(--text-muted);
}

.add {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-left: 14px;
  height: 30px;
  padding: 0 14px;
  background: var(--mark);
  border: none;
  color: var(--surface);
  font-size: 13px;
  font-weight: 500;
}

.add:hover {
  background: var(--mark-strong);
}

.add svg {
  width: 14px;
  height: 14px;
  stroke: var(--surface);
}

.signout {
  margin-left: 10px;
  height: 30px;
  padding: 0 12px;
  background: none;
  border: 1px solid var(--edge-strong);
  color: var(--text-secondary);
  font-size: 12px;
}

.signout:hover {
  border-color: var(--mark);
  color: var(--mark);
}

.picker {
  display: none;
}

.drop-target {
  position: relative;
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
}

.banner {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 18px 36px 0;
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.7;
}

.banner--doubt {
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
}

.banner--machine {
  background: var(--surface);
  border-left: 2px solid var(--machine);
  color: var(--machine);
}

.dismiss {
  margin-left: auto;
  padding: 2px 10px;
  background: none;
  border: 1px solid currentColor;
  color: inherit;
  font-size: 11px;
}

.shelf {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 22px;
  padding: 26px 36px 30px;
}

.curtain {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(58 76 120 / 8%);
  border: 2px dashed var(--machine);
  pointer-events: none;
}

.curtain span {
  padding: 8px 18px;
  background: var(--machine);
  color: var(--surface);
  font-size: 13px;
  letter-spacing: 0.04em;
}
</style>
