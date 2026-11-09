<script setup lang="ts">
/* 这次回答读了什么（界面稿⑥屏）。
 *
 * 这一屏的全部意义是把一句界面文案变成一张查得到的账。「读了 5/16 节」如果
 * 背后没有这张表，它和「模型说它读了」是同一个可信度——而这个产品卖的正好
 * 就是那点可信度。
 *
 * 三处刻意：
 *
 * 一、**没选中的章节也列出来**，带着分数、字数、名次。只列选中的，这张表就
 * 只能证明「选了什么」，证明不了「凭什么没选别的」——而后者才是用户在怀疑的事。
 *
 * 二、**整本装得下时不画排名。** 那一支连分词都没做，score 恒为 0。把一列
 * 0.00 摆成排名是在编一个不存在的判断过程。
 *
 * 三、**分数不画成百分比。** BM25 分数没有上限也没有量纲，那条相关度横杠是
 * 按本次最高分归一化的**相对**长度，旁边照实写原始分数。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError } from '@/api/client'
import { getRetrieval } from '@/api/retrieval'
import IconRail from '@/components/layout/IconRail.vue'
import type { Retrieval } from '@/types'
import BackButton from '@/components/common/BackButton.vue'

const route = useRoute()
const detail = ref<Retrieval | null>(null)
const error = ref('')

const conversationId = computed(() => Number(route.params.id))

/** 横杠长度按本次最高分归一化。绝对分数没有量纲，画成百分比是骗人。 */
const top = computed(() => Math.max(...(detail.value?.items ?? []).map((i) => i.score), 0))

const selected = computed(() => (detail.value?.items ?? []).filter((i) => i.selected))
const dropped = computed(() => (detail.value?.items ?? []).filter((i) => !i.selected))

const share = computed(() => {
  const d = detail.value
  if (!d || !d.budgetChars) return 0
  return Math.round((d.usedChars / d.budgetChars) * 100)
})

/** 一个汉字约一个 token，按 1:1 的上限估——和选章用的是同一把尺子。 */
const roughTokens = computed(() => detail.value?.usedChars ?? 0)

function bar(score: number): string {
  if (top.value <= 0) return '0%'
  return `${Math.max(2, Math.round((score / top.value) * 100))}%`
}

function n(value: number): string {
  return value.toLocaleString()
}

onMounted(async () => {
  try {
    detail.value = await getRetrieval(conversationId.value)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '这份明细打不开。'
  }
})
</script>

<template>
  <div class="shell">
    <IconRail />

    <main class="main">
      <header class="top">
        <h1 class="title">这次回答读了什么</h1>
        <span v-if="detail" class="asked">「{{ detail.question }}」</span>
        <span class="grow" />
        <span v-if="detail" class="mono meta">{{ detail.model }}</span>
        <span v-if="detail" class="mono meta faint"> 预算 {{ n(detail.budgetChars) }} 字符 </span>
        <BackButton :to="`/chat/${conversationId}`" label="回到对话" />
      </header>

      <p v-if="error" class="banner" role="alert">{{ error }}</p>

      <div v-if="detail" class="body">
        <section class="left">
          <div class="cards">
            <div class="card">
              <span class="mono cap">选中</span>
              <span class="mono big">
                {{ detail.selectedCount }}
                <span class="unit">/ {{ detail.totalCount }} 节</span>
              </span>
            </div>
            <div class="card">
              <span class="mono cap">用掉</span>
              <span class="mono big">
                {{ n(detail.usedChars) }}
                <span class="unit"
                  >字符<template v-if="share"> · {{ share }}%</template></span
                >
              </span>
            </div>
            <div class="card">
              <span class="mono cap">约合</span>
              <span class="mono big">
                {{ n(roughTokens) }}
                <span class="unit">tok</span>
              </span>
            </div>
            <div class="card card--why">
              <span class="mono cap cap--why">
                {{ detail.wholeBook ? '整本都给了模型' : '为什么不是整本' }}
              </span>
              <span class="why">
                <template v-if="detail.wholeBook">装得进上下文，没有挑。</template>
                <template v-else>
                  装不进 {{ n(detail.budgetChars) }} 字符的预算，按相关度挑了
                  {{ detail.selectedCount }} 节。
                </template>
              </span>
            </div>
          </div>

          <div class="head">
            <span class="nm">章节</span>
            <span class="kw">命中的词</span>
            <span class="grow">{{ detail.wholeBook ? '原书顺序' : '相关度' }}</span>
            <span class="num score">分</span>
            <span class="num size">字符</span>
            <span class="num acc">累计</span>
          </div>

          <div class="rows">
            <div v-for="item in selected" :key="item.rank" class="row">
              <span class="nm on">{{ item.chapterTitle }}</span>
              <span class="kw">
                <span v-for="t in item.matchedTerms.slice(0, 3)" :key="t.term" class="k">
                  {{ t.term }} ×{{ t.count }}
                </span>
              </span>
              <span class="grow track">
                <span class="fill" :style="{ width: bar(item.score) }" />
              </span>
              <span class="mono num score on-score">{{ item.score.toFixed(2) }}</span>
              <span class="mono num size">{{ n(item.wordCount) }}</span>
              <span class="mono num acc">{{ n(item.cumulativeChars) }}</span>
            </div>

            <div v-if="detail.shortfall" class="line">
              <!-- 排版靠 gap，不靠全角空格：U+3000 是 ESLint 眼里的不规则空白，
                   而且它在等宽字体里的宽度和别处对不齐 -->
              <span class="mono line-cap">预算线</span>
              <span class="mono line-cap">{{ n(detail.budgetChars) }} 字符</span>
              <span class="grow" />
              <span class="line-text">
                下一节「{{ detail.shortfall.title }}」<b
                  >差 {{ n(detail.shortfall.short) }} 字符装不下</b
                >
              </span>
            </div>

            <div v-for="item in dropped" :key="item.rank" class="row">
              <span class="nm off">{{ item.chapterTitle }}</span>
              <span class="kw">
                <span v-for="t in item.matchedTerms.slice(0, 3)" :key="t.term" class="k k--off">
                  {{ t.term }} ×{{ t.count }}
                </span>
              </span>
              <span class="grow track">
                <span class="fill fill--off" :style="{ width: bar(item.score) }" />
              </span>
              <span class="mono num score off">{{ item.score.toFixed(2) }}</span>
              <span class="mono num size off">{{ n(item.wordCount) }}</span>
              <span class="mono num acc dash">—</span>
            </div>
          </div>
        </section>

        <aside class="right">
          <span class="mono rail-cap">怎么挑的</span>

          <div v-if="detail.wholeBook" class="note">
            <span class="note-title">那一列分数是 0，不是「不相关」</span>
          </div>

          <template v-else>
            <div class="note">
              <span class="note-title">装不下就停，不截半节</span>
            </div>

            <div class="note">
              <span class="note-title">字符含标点和 Markdown 标记，比目录页的字数略大</span>
            </div>

            <div v-if="detail.queryTerms.length" class="note">
              <span class="note-title">参与打分的词</span>
              <span class="terms">
                <span v-for="t in detail.queryTerms" :key="t" class="k">{{ t }}</span>
              </span>
            </div>
          </template>
        </aside>
      </div>
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

.main {
  flex-grow: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.top {
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 22px 30px 15px;
  border-bottom: 1px solid var(--rule);
}

.title {
  margin: 0;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 20px;
  letter-spacing: 0.04em;
}

.asked {
  min-width: 0;
  font-size: 12.5px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.grow {
  flex-grow: 1;
}

.meta {
  font-size: 11px;
  color: var(--text-secondary);
}

.faint {
  color: var(--text-faint);
}

.banner {
  margin: 14px 30px;
  padding: 11px 15px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
  line-height: 1.8;
}

.body {
  flex-grow: 1;
  display: flex;
  min-width: 0;
  overflow: hidden;
}

.left {
  flex-grow: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 16px 30px 0;
  overflow: hidden;
}

.cards {
  display: flex;
  flex-shrink: 0;
  border: 1px solid var(--edge);
  background: var(--surface);
  margin-bottom: 16px;
}

.card {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 12px 16px;
  border-right: 1px solid var(--hair);
}

.card--why {
  flex: 1.6;
  border-right: none;
}

.cap {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--text-faint);
}

/* 黄檗 = 要你判断的地方。「为什么不是整本」正是那种地方 */
.cap--why {
  color: var(--doubt);
}

.big {
  font-size: 17px;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.unit {
  font-size: 12px;
  color: var(--text-faint);
}

.why {
  font-size: 11.5px;
  line-height: 1.6;
  color: #6b5218;
}

.head,
.row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.head {
  flex-shrink: 0;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--edge-strong);
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.12em;
  color: var(--text-faint);
}

.rows {
  flex-grow: 1;
  overflow-y: auto;
  padding-bottom: 20px;
}

.row {
  padding: 5px 0;
  border-bottom: 1px solid var(--hair);
}

.nm {
  width: 110px;
  flex-shrink: 0;
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.on {
  font-weight: 500;
}

.off {
  color: var(--text-faint);
}

.kw {
  width: 216px;
  flex-shrink: 0;
  display: flex;
  gap: 5px;
  overflow: hidden;
}

.k {
  padding: 2px 6px;
  background: var(--hair);
  font-family: var(--font-mono);
  font-size: 9.5px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.k--off {
  background: var(--panel);
  color: var(--text-faint);
}

.track {
  height: 5px;
  background: var(--hair);
}

/* 朱批 = 人这次要的东西。选中的那几节是这个问题挑出来的 */
.fill {
  display: block;
  height: 100%;
  background: var(--mark);
}

.fill--off {
  background: #c2c6be;
}

.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  color: var(--text-secondary);
}

.score {
  width: 48px;
  flex-shrink: 0;
}

.size {
  width: 52px;
  flex-shrink: 0;
}

.acc {
  width: 58px;
  flex-shrink: 0;
}

.on-score {
  color: var(--mark);
}

.dash {
  color: #c2c6be;
}

.line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 3px 0 4px;
  padding: 9px 0 10px;
  border-top: 1px dashed var(--mark);
  border-bottom: 1px dashed var(--mark);
}

.line-cap {
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--mark);
}

.line-text {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.line-text b {
  font-weight: 500;
}

.right {
  width: 330px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 17px;
  padding: 20px 24px 0;
  border-left: 1px solid var(--rule);
  background: var(--panel);
  overflow-y: auto;
}

.rail-cap {
  font-size: 10px;
  letter-spacing: 0.18em;
  color: var(--text-faint);
}

.note {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.note-title {
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 13.5px;
}

.terms {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
</style>
