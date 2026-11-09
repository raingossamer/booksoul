<script setup lang="ts">
/* 解析报告（界面稿②屏）：一本可以逐条核对的账。
 *
 * 这一屏存在的理由是那句话——**解析成功不等于解析正确**。所以它的每一行都必须
 * 是真数出来的。引擎没数的项后端整行不返回，这里也就不会出现；不填 0，
 * 因为 0 会被读成「一处都没动」，那是假的。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { getReport, resolveNote } from '@/api/books'
import { ApiError } from '@/api/client'
import NoteBanner from '@/components/common/NoteBanner.vue'
import AppShell from '@/components/layout/AppShell.vue'
import BackButton from '@/components/common/BackButton.vue'
import type { Note, Report } from '@/types'

const route = useRoute()
const report = ref<Report | null>(null)
const error = ref('')

const bookId = computed(() => Number(route.params.id))
const unresolved = computed(() => report.value?.notes.filter((n) => !n.resolved) ?? [])

const headline = computed(() => {
  const r = report.value
  if (!r) return ''
  const mb = (r.sourceSizeBytes / 1024 / 1024).toFixed(1)
  const secs = r.elapsedSeconds === null ? '' : ` · 用时 ${r.elapsedSeconds.toFixed(1)}s`
  return `${mb} MB${secs}`
})

function humanSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

async function markResolved(note: Note) {
  try {
    await resolveNote(bookId.value, note.id)
    note.resolved = true
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '标记没能保存。'
  }
}

onMounted(async () => {
  try {
    report.value = await getReport(bookId.value)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '报告读不出来。'
  }
})
</script>

<template>
  <AppShell title="解析报告" :hint="report?.sourceFile">
    <template #head-right>
      <BackButton v-if="report" :to="`/books/${report.bookId}`" label="回书籍详情" />
    </template>

    <p v-if="error" class="banner" role="alert">{{ error }}</p>

    <div v-else-if="report" class="split">
      <div class="ledger">
        <div class="cap-line">
          <span class="mono cap">这一次解析</span>
          <span class="mono sub">{{ headline }}</span>
        </div>

        <div class="rows">
          <div v-for="row in report.ledger" :key="row.label" class="row">
            <span class="label">{{ row.label }}</span>
            <span class="detail">{{ row.detail }}</span>
          </div>
        </div>

        <div v-if="unresolved.length" class="notes">
          <span class="mono cap cap--doubt">要你看一眼的 {{ unresolved.length }} 处</span>
          <NoteBanner
            v-for="note in unresolved"
            :key="note.id"
            :note="note"
            @resolve="markResolved"
          />
        </div>
      </div>

      <aside class="outputs">
        <span class="mono cap">写出来的</span>
        <div class="files">
          <div class="mono file file--root">{{ report.storagePath }}/</div>
          <div v-for="file in report.outputs" :key="file.path" class="mono file">
            <span class="fname">{{ file.path }}</span>
            <span class="fsize">
              {{ file.isDir ? `${file.childCount} 个` : humanSize(file.sizeBytes) }}
            </span>
          </div>
        </div>
        <p class="takeaway">都是普通 Markdown，可以直接拿去用。</p>
      </aside>
    </div>
  </AppShell>
</template>

<style scoped>
.banner {
  margin: 30px 40px;
  padding: 12px 16px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
}

.split {
  flex-grow: 1;
  display: flex;
  min-height: 0;
}

.ledger {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 32px 24px;
  min-width: 0;
  overflow-y: auto;
}

.cap-line {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.cap {
  font-size: 10px;
  letter-spacing: 0.18em;
  color: var(--text-faint);
}

.cap--doubt {
  color: var(--doubt);
}

.sub {
  font-size: 11px;
  color: var(--text-secondary);
}

.rows {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.row {
  display: flex;
  gap: 14px;
  padding: 8px 15px;
  border-bottom: 1px solid var(--hair);
}

.row:last-child {
  border-bottom: none;
}

.label {
  width: 72px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-muted);
}

.detail {
  font-size: 12.5px;
  line-height: 1.7;
}

.notes {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.outputs {
  width: 352px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px 26px;
  border-left: 1px solid var(--rule);
  background: var(--panel);
  overflow-y: auto;
}

.files {
  display: flex;
  flex-direction: column;
  padding: 14px 15px;
  background: var(--surface);
  border: 1px solid var(--edge);
  font-size: 11.5px;
}

.file {
  display: flex;
  padding: 3px 0;
  color: var(--text-secondary);
}

.file--root {
  color: var(--text);
}

.fname {
  flex-grow: 1;
  padding-left: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fsize {
  color: var(--text-faint);
}

.takeaway {
  margin: 0;
  font-size: 12px;
  line-height: 1.85;
  color: var(--text-secondary);
}
</style>
