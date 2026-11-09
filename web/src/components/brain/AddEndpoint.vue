<script setup lang="ts">
/* 加一个自己的 OpenAI 兼容端点（界面稿⑧屏那一行「+ 添加自定义端点」）。
 *
 * 只收 OpenAI 兼容这一种。anthropic 那条路要官方 SDK，填个地址是接不上的——
 * 给出这个选项只会让人填出一个连不通的配置，然后在提问那一刻才发现。
 *
 * 表单里**没有 key 这一栏**。端点建好之后，key 在那一行自己的输入框里填，
 * 存进 localStorage。两件事分开做是刻意的：端点是要上传到服务器的配置，
 * key 是绝不上传的东西，混在一个表单里迟早有人把它们一起 POST 出去。
 */
import { computed, ref } from 'vue'

const emit = defineEmits<{
  submit: [payload: { name: string; model: string; baseUrl: string; contextTokens: number }]
  cancel: []
}>()

defineProps<{ busy: boolean; error: string }>()

const name = ref('')
const model = ref('')
const baseUrl = ref('')
const contextTokens = ref('')

const ready = computed(() => name.value.trim() && model.value.trim() && baseUrl.value.trim())

function submit() {
  if (!ready.value) return
  emit('submit', {
    name: name.value.trim(),
    model: model.value.trim(),
    baseUrl: baseUrl.value.trim(),
    contextTokens: Number(contextTokens.value) || 0
  })
}
</script>

<template>
  <form class="add" @submit.prevent="submit">
    <div class="fields">
      <label class="field">
        <span class="mono cap">名字</span>
        <input v-model="name" maxlength="32" placeholder="书房那台" />
      </label>
      <label class="field field--wide">
        <span class="mono cap">端点地址</span>
        <input
          v-model="baseUrl"
          class="mono"
          maxlength="2000"
          placeholder="http://192.168.1.9:8000/v1"
          spellcheck="false"
        />
      </label>
      <label class="field">
        <span class="mono cap">模型</span>
        <input v-model="model" class="mono" maxlength="64" placeholder="qwen3-32b" />
      </label>
      <label class="field field--narrow">
        <span class="mono cap">上下文 token</span>
        <input v-model="contextTokens" class="mono" inputmode="numeric" placeholder="32768" />
      </label>
    </div>

    <p class="hint">地址里不要带 key。上下文不知道可以留空。</p>

    <p v-if="error" class="err" role="alert">{{ error }}</p>

    <div class="acts">
      <button class="go" type="submit" :disabled="!ready || busy">
        {{ busy ? '加着…' : '加进来' }}
      </button>
      <button class="link" type="button" @click="emit('cancel')">取消</button>
    </div>
  </form>
</template>

<style scoped>
.add {
  display: flex;
  flex-direction: column;
  gap: 11px;
  padding: 15px 14px 16px;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.fields {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 150px;
}

.field--wide {
  flex-grow: 1;
  min-width: 260px;
  width: auto;
}

.field--narrow {
  width: 118px;
}

.cap {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--text-faint);
}

input {
  height: 28px;
  padding: 0 9px;
  background: var(--paper);
  border: 1px solid var(--edge-strong);
  font-size: 12px;
  color: var(--text);
  min-width: 0;
}

input:focus {
  outline: none;
  border-color: var(--mark);
}

.hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.8;
  color: var(--text-faint);
}

.err {
  margin: 0;
  padding: 9px 12px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 11.5px;
  line-height: 1.8;
}

.acts {
  display: flex;
  align-items: center;
  gap: 14px;
}

.go {
  height: 30px;
  padding: 0 18px;
  background: var(--mark);
  border: none;
  color: var(--surface);
  font-size: 12.5px;
  font-weight: 500;
}

.go:disabled {
  background: var(--edge-strong);
  color: var(--text-faint);
}

.link {
  padding: 0;
  background: none;
  border: none;
  font-size: 12px;
  color: var(--text-secondary);
}

.link:hover {
  color: var(--mark);
}
</style>
