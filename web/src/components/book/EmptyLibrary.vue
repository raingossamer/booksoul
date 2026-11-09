<script setup lang="ts">
/* 初次启动态（界面稿⓪屏）。
 *
 * 格式那一排由 GET /api/formats 的运行时探测驱动，不写死：缺了 pymupdf 的部署
 * 不该还对外宣称支持 PDF。原先这里写死了一张表，而导入那一屏、上传前的粗筛读的
 * 都是探测结果——同一件事两处说法，迟早一处说收、一处说不收。
 *
 * 拖放本身由外层 LibraryView 接（整个内容区都是拖放区），这里只把「选择文件」
 * 这个动作抛出去——书库里已经有书时这一屏不在，但拖放该照样成立。
 */
defineProps<{
  /** 这个部署收的格式（展示名），探测结果还没回来时是空的 */
  formats: string[]
}>()

defineEmits<{ pick: [] }>()
</script>

<template>
  <div class="empty">
    <section class="pitch">
      <h2>把一本书交给模型之前，<br />先让它变成模型读得懂的样子。</h2>
      <div class="rule" />
    </section>

    <section class="drop" aria-label="上传书籍">
      <svg
        class="arrow"
        viewBox="0 0 24 24"
        fill="none"
        stroke-width="1.2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M12 3v12" />
        <path d="M7.5 10.5L12 15l4.5-4.5" />
        <path d="M4 18v3h16v-3" />
      </svg>

      <div class="drop-text">
        <span class="lead">把书拖到这里</span>
        <span class="sub">文件或整个目录都行</span>
      </div>

      <button class="pick" type="button" @click="$emit('pick')">选择文件</button>

      <div class="formats">
        <span v-for="format in formats" :key="format" class="mono chip">
          {{ format }}
        </span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.empty {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 34px;
  padding: 24px 40px;
  overflow-y: auto;
}

.pitch {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 15px;
  max-width: 640px;
}

.pitch h2 {
  font-size: 25px;
  line-height: 1.7;
  letter-spacing: 0.05em;
  text-align: center;
}

.rule {
  width: 40px;
  height: 2px;
  background: var(--mark);
}

.drop {
  width: 660px;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 40px 40px 34px;
  border: 1px dashed var(--dash);
  background: var(--surface);
}

.arrow {
  width: 34px;
  height: 34px;
  stroke: var(--mark);
}

.drop-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 9px;
}

.lead {
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 16px;
  letter-spacing: 0.04em;
}

.sub {
  font-size: 12px;
  color: var(--text-muted);
}

.pick {
  height: 34px;
  padding: 0 18px;
  background: var(--mark);
  border: none;
  color: var(--surface);
  font-size: 13px;
  font-weight: 500;
}

.pick:hover {
  background: var(--mark-strong);
}

.formats {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  justify-content: center;
}

.chip {
  padding: 3px 9px;
  border: 1px solid var(--edge);
  font-size: 10.5px;
  letter-spacing: 0.1em;
  color: var(--text-muted);
}
</style>
