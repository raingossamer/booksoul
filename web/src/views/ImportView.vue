<script setup lang="ts">
/* 导入与解析（界面稿①屏）。
 *
 * 这一屏的全部意思是：把解析过程摊开给人看。一批文件各自一行、各自一条 SSE、
 * 各自的五阶段进度；失败的那一行不只说「失败了」，还给出这个文件自己的补救命令。
 */
import { computed, onMounted, ref } from 'vue'

import ImportRow from '@/components/book/ImportRow.vue'
import AppShell from '@/components/layout/AppShell.vue'
import { useFileDrop } from '@/composables/useFileDrop'
import { useImports } from '@/composables/useImports'

const { items, add, skipFolders, forget, formats } = useImports()

const picker = ref<HTMLInputElement | null>(null)
const { dragging, listeners: drop } = useFileDrop(({ files, emptyFolders }) => {
  skipFolders(emptyFolders)
  if (files.length) add(files)
}, formats.recognizes)

const hint = computed(() => {
  if (!items.value.length) return '等着收书'
  const working = items.value.filter((i) => i.state === 'parsing' || i.state === 'uploading')
  return working.length ? `${working.length} 个在解析` : `${items.value.length} 个已处理`
})

function openPicker() {
  picker.value?.click()
}

function onPicked(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) add(input.files)
  input.value = ''
}

onMounted(formats.load)
</script>

<template>
  <AppShell title="导入与解析" :hint="hint">
    <template #head-right>
      <button class="add" type="button" @click="openPicker">选择文件</button>
    </template>

    <input
      ref="picker"
      class="picker"
      type="file"
      multiple
      :accept="formats.accept.value"
      @change="onPicked"
    />

    <div class="body" v-on="drop">
      <div v-if="formats.unavailable.value.length" class="banner">
        暂不支持<span v-for="f in formats.unavailable.value" :key="f.key" class="mono off">{{
          f.label
        }}</span
        >，换个格式再传。
      </div>

      <div class="rows">
        <ImportRow v-for="item in items" :key="item.key" :item="item" @forget="forget" />
      </div>

      <div class="spacer" />

      <div class="dropzone" :class="{ 'dropzone--over': dragging }" @click="openPicker">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M12 4v11" />
          <path d="M8 11l4 4 4-4" />
          <path d="M4 19h16" />
        </svg>
        <span>
          {{ dragging ? '松手就开始解析' : '拖入文件或整个目录，或者点这里选' }}
        </span>
        <span class="mono limits">
          <template v-for="f in formats.available.value" :key="f.key">
            {{ f.label }}<em v-if="f.experimental" :title="f.note">*</em>
          </template>
          · 单个最大 {{ formats.maxUploadMb.value }} MB
        </span>
      </div>
    </div>
  </AppShell>
</template>

<style scoped>
.add {
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

.picker {
  display: none;
}

.body {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 22px 40px 26px;
  overflow-y: auto;
}

.banner {
  padding: 10px 14px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
  line-height: 1.8;
}

.off {
  margin: 0 4px;
  font-size: 11px;
  letter-spacing: 0.1em;
}

.rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.spacer {
  flex-grow: 1;
  min-height: 12px;
}

.dropzone {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 18px;
  background: var(--surface);
  border: 1px dashed var(--edge-strong);
  color: var(--text-muted);
  font-size: 12.5px;
  cursor: pointer;
}

.dropzone--over {
  border-color: var(--machine);
  color: var(--machine);
}

.dropzone svg {
  width: 16px;
  height: 16px;
  stroke: currentColor;
}

.limits {
  font-size: 10.5px;
  color: var(--text-faint);
  letter-spacing: 0.06em;
}

.limits em {
  font-style: normal;
  color: var(--doubt);
}
</style>
