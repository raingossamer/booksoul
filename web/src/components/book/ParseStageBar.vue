<script setup lang="ts">
/* 解析五阶段（界面稿①屏）：读取 › 解析目录 › 转换正文 › 清洗 › 写入。
 *
 * 当前阶段用靛色——机器在做事。走过的用深灰，没到的用浅灰。
 * 只有「转换正文」带得出分节刻度，其余阶段不编百分比：
 * 一个假的进度比没有进度更误导人。
 */
import { computed } from 'vue'

import type { ParseStage } from '@/types'

const props = defineProps<{ stage: ParseStage; done: number; total: number }>()

const STAGES: { key: ParseStage; label: string }[] = [
  { key: 'read', label: '读取' },
  { key: 'toc', label: '解析目录' },
  { key: 'convert', label: '转换正文' },
  { key: 'clean', label: '清洗' },
  { key: 'write', label: '写入' }
]

const currentIndex = computed(() => STAGES.findIndex((s) => s.key === props.stage))

const detail = computed(() => {
  if (props.total <= 0) return ''
  const label = STAGES[currentIndex.value]?.label ?? ''
  return `${label} ${props.done}/${props.total} 节`
})
</script>

<template>
  <div class="bar">
    <template v-for="(item, i) in STAGES" :key="item.key">
      <span v-if="i > 0" class="mono sep">›</span>
      <span
        class="mono step"
        :class="{
          'step--done': i < currentIndex,
          'step--now': i === currentIndex,
          'step--todo': i > currentIndex
        }"
      >
        {{ item.label }}
      </span>
    </template>
    <span class="grow" />
    <span v-if="detail" class="mono detail">{{ detail }}</span>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  align-items: center;
}

.step {
  font-size: 10.5px;
}

.step--done {
  color: var(--text-secondary);
}

.step--now {
  color: var(--machine);
  font-weight: 500;
}

.step--todo {
  color: #b4bac2;
}

.sep {
  padding: 0 8px;
  font-size: 10.5px;
  color: var(--edge-strong);
}

.grow {
  flex-grow: 1;
}

.detail {
  font-size: 10.5px;
  color: var(--text-faint);
}
</style>
