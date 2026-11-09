<script setup lang="ts">
/* 弹窗。用原生 `<dialog>` 的 `showModal()`，不手写遮罩：焦点困在里面、Esc 关闭、
 * 背后那一层变成不可交互——这三件事浏览器都管了，手写就得各写一遍，漏了哪件都不报错。
 *
 * 开关由父组件的 `v-if` 管：挂上就打开，要关就发 `close`，由父组件把它摘掉。
 * Esc 也走这条路（拦下 `cancel`），不让浏览器自己把它关了、父组件却还以为开着。
 */
import { onMounted, ref, useId } from 'vue'

defineProps<{ title: string }>()

const emit = defineEmits<{ close: [] }>()

const dialog = ref<HTMLDialogElement | null>(null)
const titleId = useId()

// 按下与松开都在遮罩上才算「点了遮罩」。只看 click 的话，在搜索框里拖选文字、
// 松手时指针落到了框外，也会被当成点遮罩——改了一半的东西就这么没了
let pressedOutside = false

function onPointerDown(event: PointerEvent) {
  pressedOutside = event.target === dialog.value
}

function onClick(event: MouseEvent) {
  if (pressedOutside && event.target === dialog.value) emit('close')
  pressedOutside = false
}

onMounted(() => dialog.value?.showModal())
</script>

<template>
  <dialog
    ref="dialog"
    class="dialog"
    :aria-labelledby="titleId"
    @cancel.prevent="emit('close')"
    @pointerdown="onPointerDown"
    @click="onClick"
  >
    <div class="frame">
      <header class="head">
        <h2 :id="titleId" class="title">{{ title }}</h2>
        <button class="close" type="button" aria-label="关闭" @click="emit('close')">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round">
            <path d="M6 6l12 12" />
            <path d="M18 6L6 18" />
          </svg>
        </button>
      </header>
      <div class="body">
        <slot />
      </div>
    </div>
  </dialog>
</template>

<style scoped>
/* 整块就是那张卡：dialog 自己不留内边距，点在它身上 = 点在遮罩上 */
.dialog {
  width: min(640px, calc(100vw - 32px));
  max-height: calc(100vh - 64px);
  padding: 0;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
  color: var(--text);
  box-shadow: 0 16px 48px color-mix(in srgb, var(--ink) 22%, transparent);
}

.dialog::backdrop {
  background: color-mix(in srgb, var(--ink) 38%, transparent);
}

.frame {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 66px);
}

.head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 14px 12px 18px;
  border-bottom: 1px solid var(--hair);
}

.title {
  flex-grow: 1;
  margin: 0;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 15px;
  letter-spacing: 0.04em;
}

.close {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  background: none;
  border: 1px solid transparent;
}

.close svg {
  width: 16px;
  height: 16px;
  stroke: var(--text-muted);
}

.close:hover svg {
  stroke: var(--mark);
}

.close:focus-visible {
  outline: none;
  border-color: var(--mark);
}

.body {
  min-height: 0;
  overflow-y: auto;
}
</style>
