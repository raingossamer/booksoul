<script setup lang="ts">
/* ①屏里一个文件的一行。四种形态，色彩按语义走：
 *   解析中 → 靛（机器在做事）
 *   已入库 → **不着色**，只给数字
 *   停下了 → 重墨整块，外加一条可以直接复制执行的命令
 *   没收下 → 黄檗（等人判断：换个文件，或者换个格式）
 */
import { computed, ref } from 'vue'

import ParseStageBar from './ParseStageBar.vue'
import type { ImportItem } from '@/composables/useImports'

const props = defineProps<{ item: ImportItem }>()
defineEmits<{ forget: [key: string] }>()

const copied = ref(false)

/** remedy 的第一行是说明，其余是命令。命令要能整段复制。 */
const explanation = computed(() => props.item.remedy.split('\n')[0] ?? '')
const command = computed(() => props.item.remedy.split('\n').slice(1).join('\n').trim())

const stats = computed(() => {
  const s = props.item.stats
  const out: string[] = []
  if (s['章节']) out.push(`${s['章节']} 节`)
  if (s['字数']) out.push(`${s['字数'].toLocaleString()} 字`)
  if (s['脚注']) out.push(`脚注 ${s['脚注']}`)
  if (s['图片']) out.push(`图 ${s['图片']}`)
  return out
})

async function copyCommand() {
  try {
    await navigator.clipboard.writeText(command.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 1600)
  } catch {
    // 非 HTTPS 或没授权时 clipboard 不可用。命令本身是选得中的文本，手动复制照样行。
  }
}
</script>

<template>
  <!-- 停下了 -->
  <div v-if="item.state === 'failed'" class="row row--blocked">
    <div class="line">
      <span class="mono name">{{ item.filename }}</span>
      <span v-if="item.errorCode === 'no_text_layer'" class="mono tag">没有文字层</span>
      <span class="grow" />
      <button class="mono dismiss dismiss--dark" type="button" @click="$emit('forget', item.key)">
        收起
      </button>
    </div>
    <p class="why">{{ explanation || item.reason }}</p>
    <div v-if="command" class="cmd">
      <span class="mono cmd-text">{{ command }}</span>
      <button class="mono copy" type="button" @click="copyCommand">
        {{ copied ? '已复制' : '复制' }}
      </button>
    </div>
  </div>

  <!-- 没收下：上传前就被挡掉的 -->
  <div v-else-if="item.state === 'rejected'" class="row row--doubt">
    <div class="line">
      <span class="mono name name--dark">{{ item.filename }}</span>
      <span class="reason">{{ item.reason }}</span>
      <span class="grow" />
      <button class="mono dismiss" type="button" @click="$emit('forget', item.key)">收起</button>
    </div>
  </div>

  <!-- 已入库 -->
  <div v-else-if="item.state === 'ready'" class="row row--done">
    <div class="line">
      <!-- 入库之后最常做的下一步是打开它，不是看报告：书名本身就是去书籍详情的路 -->
      <RouterLink class="title" :to="`/books/${item.bookId}`">{{ item.filename }}</RouterLink>
      <div v-if="stats.length" class="mono numbers">
        <span v-for="n in stats" :key="n">{{ n }}</span>
      </div>
      <span class="grow" />
      <span class="mono source">{{ item.detectedFormat.toUpperCase() }}</span>
      <RouterLink class="report" :to="`/books/${item.bookId}/report`">看解析报告</RouterLink>
    </div>
  </div>

  <!-- 解析中 / 上传中 -->
  <div v-else class="row row--working">
    <div class="line">
      <span class="mono name name--dark">{{ item.filename }}</span>
      <span class="mono working">{{ item.state === 'uploading' ? '上传中' : '解析中' }}</span>
    </div>
    <ParseStageBar
      v-if="item.state === 'parsing'"
      :stage="item.stage"
      :done="item.done"
      :total="item.total"
    />
  </div>
</template>

<style scoped>
.row {
  display: flex;
  flex-direction: column;
  gap: 11px;
  padding: 16px 20px;
}

.line {
  display: flex;
  align-items: center;
  gap: 14px;
}

.grow {
  flex-grow: 1;
}

.name {
  font-size: 13px;
  color: var(--paper);
}

.name--dark {
  color: var(--text);
}

.title {
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 15px;
  color: var(--text);
  text-decoration: none;
}

.title:hover {
  color: var(--mark);
}

/* ---- 解析中 ---- */
.row--working {
  background: var(--surface);
  border: 1px solid var(--edge);
  border-left: 3px solid var(--machine);
}

.working {
  font-size: 11px;
  color: var(--machine);
}

/* ---- 已入库：不着色 ---- */
.row--done {
  background: var(--surface);
  border: 1px solid var(--edge);
}

.numbers {
  display: flex;
  gap: 22px;
  padding-left: 12px;
  border-left: 1px solid var(--hair);
  font-size: 11.5px;
  color: var(--text-secondary);
}

.source {
  font-size: 10.5px;
  color: var(--text-faint);
}

.report {
  font-size: 12px;
  color: var(--mark);
  text-decoration: none;
  border-bottom: 1px solid #ddb5b1;
}

/* ---- 存疑 / 没收下 ---- */
.row--doubt {
  background: var(--doubt-bg);
  border-left: 3px solid var(--doubt);
}

.reason {
  font-size: 12px;
  color: var(--doubt);
  line-height: 1.7;
}

/* ---- 停下了 ---- */
.row--blocked {
  background: var(--ink);
  padding: 18px 20px;
  gap: 13px;
}

.tag {
  padding: 2px 7px;
  background: var(--ink-text);
  color: var(--ink);
  font-size: 9.5px;
  letter-spacing: 0.14em;
}

.why {
  margin: 0;
  max-width: 760px;
  font-size: 12.5px;
  line-height: 1.85;
  color: #c2c8d0;
}

.cmd {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  background: var(--rail-active);
  border-left: 2px solid var(--ink-text);
}

.cmd-text {
  flex-grow: 1;
  font-size: 12px;
  color: var(--ink-text);
  user-select: all;
  word-break: break-all;
}

.copy {
  padding: 3px 10px;
  background: none;
  border: 1px solid var(--rail-icon);
  color: var(--ink-text);
  font-size: 11px;
}

.dismiss {
  padding: 2px 10px;
  background: none;
  border: 1px solid currentColor;
  color: var(--doubt);
  font-size: 11px;
}

.dismiss--dark {
  color: var(--text-muted);
}
</style>
