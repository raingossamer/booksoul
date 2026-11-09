<script setup lang="ts">
/* 存疑（黄檗）与阻断（重墨）两种提示条。
 *
 * 两种颜色管两件事，不混用：
 *   黄檗 = 等你判断。系统觉得可疑但它分不清，要人看一眼。
 *   重墨 = 停下来了。这不是可疑，是走不下去。
 *
 * 阻断态用整块墨底而不是第二种红 —— 红色在这套视觉里归「人做的标记」，
 * 借去表示错误会把两件事搅在一起。
 */
import type { Note } from '@/types'

defineProps<{ note: Note }>()
defineEmits<{ resolve: [note: Note] }>()
</script>

<template>
  <div class="banner" :class="note.severity === 'block' ? 'banner--block' : 'banner--doubt'">
    <svg
      v-if="note.severity === 'doubt'"
      class="icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke-width="1.9"
      stroke-linecap="round"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v5" />
      <path d="M12 16.5v.01" />
    </svg>
    <span v-else class="mono tag">停下了</span>

    <span class="text">{{ note.message }}</span>

    <button v-if="!note.resolved" class="act" type="button" @click="$emit('resolve', note)">
      核对过了
    </button>
    <span v-else class="mono done">已核对</span>
  </div>
</template>

<style scoped>
.banner {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 11px 14px;
}

.banner--doubt {
  background: var(--doubt-bg);
  border: 1px solid #e5d5ae;
}

.banner--block {
  background: var(--ink);
}

.icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  margin-top: 2px;
  stroke: var(--doubt);
}

.tag {
  flex-shrink: 0;
  margin-top: 1px;
  padding: 2px 6px;
  background: var(--ink-text);
  color: var(--ink);
  font-size: 9.5px;
  letter-spacing: 0.12em;
}

.text {
  flex-grow: 1;
  font-size: 12px;
  line-height: 1.75;
  color: #6b5218;
}

.banner--block .text {
  color: #c2c8d0;
}

.act {
  flex-shrink: 0;
  padding: 0 0 1px;
  background: none;
  border: none;
  border-bottom: 1px solid #c9a961;
  color: var(--doubt);
  font-size: 12px;
  white-space: nowrap;
}

.banner--block .act {
  border-bottom-color: #5a626e;
  color: var(--ink-text);
}

.done {
  flex-shrink: 0;
  font-size: 10.5px;
  color: var(--text-faint);
}
</style>
