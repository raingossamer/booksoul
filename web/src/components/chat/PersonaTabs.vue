<script setup lang="ts">
/* 三种人格（界面稿⑦屏那条切换条）。
 *
 * 描述那一行不是装饰：三种人格给出的是**性质不同**的回应，不是同一个答案的
 * 三种语气。不写清楚，用户会以为自己在选一个皮肤。
 *
 * 「对读」是第四个格子，它不是第四种人格——它是「三个一起看」。
 * 点它**不弹确认**（人工卡点已裁决）：三路共享整本书的缓存，真实成本不是三倍，
 * 而对读正是这个产品的立意；每次多一步点击会把它变成一个没人用的功能。
 * 代价写在它下面那行小字上，真实账单在底部仪表和⑥屏。
 *
 * 「处境模式」（S11）是右边那个开关，不是第五个格子——它和选哪种人格是两个
 * 正交的问题：三种人格都可以带着它问。开关也**不弹确认**，理由和对读那条
 * 不同：缓存断点在全书之后、人格之前，附加那一段只动断点之后那一块，
 * 整本书的缓存照常命中。它是这一屏上少数几个**真的不花额外的钱**的动作，
 * 所以右边那行小字写的是「不废缓存」，而不是一句代价。
 */
import type { PersonaKey } from '@/types'

defineProps<{
  current: PersonaKey
  comparing?: boolean
  disabled?: boolean
  reflection?: boolean
}>()
defineEmits<{ pick: [persona: PersonaKey]; compare: []; reflect: [on: boolean] }>()

const PERSONAS: { key: PersonaKey; name: string; desc: string }[] = [
  { key: 'scholar', name: '研读者', desc: '引原文 · 分清事实与推演' },
  { key: 'author', name: '作者', desc: '用他的语气与困惑' },
  { key: 'book', name: '书', desc: '只有自己这些字' }
]
</script>

<template>
  <div class="tabs">
    <button
      v-for="persona in PERSONAS"
      :key="persona.key"
      type="button"
      class="tab"
      :class="{ 'tab--on': !comparing && persona.key === current }"
      :disabled="disabled"
      @click="$emit('pick', persona.key)"
    >
      <span class="name">{{ persona.name }}</span>
      <span class="desc">{{ persona.desc }}</span>
    </button>

    <button
      type="button"
      class="tab"
      :class="{ 'tab--on': comparing }"
      :disabled="disabled"
      @click="$emit('compare')"
    >
      <span class="name">对读</span>
      <span class="desc">三个一起看 · 三次调用</span>
    </button>
    <span class="grow" />

    <button
      type="button"
      class="reflect"
      :class="{ 'reflect--on': reflection }"
      :disabled="disabled"
      role="switch"
      :aria-checked="Boolean(reflection)"
      @click="$emit('reflect', !reflection)"
    >
      <span class="knob" />
      <span class="reflect-text">
        <span class="name">处境模式</span>
        <span class="desc">{{
          reflection ? '带着你的处境问 · 不废缓存' : '问的是你，不是书'
        }}</span>
      </span>
    </button>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 0 30px;
  border-bottom: 1px solid var(--rule);
  background: var(--panel);
}

.tab {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 9px 15px 8px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  text-align: left;
}

.tab:disabled {
  opacity: 0.55;
}

/* 朱批 = 你选的那个。选人格是人做的动作 */
.tab--on {
  border-bottom-color: var(--mark);
}

.name {
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 13.5px;
  color: var(--text-secondary);
}

.tab--on .name {
  font-weight: 700;
  color: var(--mark);
}

.desc {
  font-size: 10.5px;
  color: var(--text-faint);
}

.grow {
  flex-grow: 1;
}

.reflect {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 0 8px 15px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  text-align: left;
}

.reflect:disabled {
  opacity: 0.55;
}

.knob {
  position: relative;
  width: 28px;
  height: 15px;
  flex-shrink: 0;
  background: var(--edge-strong);
  border-radius: 8px;
  transition: background 0.12s;
}

.knob::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 11px;
  height: 11px;
  background: var(--surface);
  border-radius: 50%;
  transition: transform 0.12s;
}

/* 朱批 = 人做的动作。开着的时候这一路问的是「你」，不是「书」 */
.reflect--on .knob {
  background: var(--mark);
}

.reflect--on .knob::after {
  transform: translateX(13px);
}

.reflect-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.reflect--on .name {
  font-weight: 700;
  color: var(--mark);
}

@media (prefers-reduced-motion: reduce) {
  .knob,
  .knob::after {
    transition: none;
  }
}
</style>
