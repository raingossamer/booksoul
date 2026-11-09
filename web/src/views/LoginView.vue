<script setup lang="ts">
/* 登录 / 注册。
 *
 * 界面稿九屏从「初次启动」开始，没有覆盖这一页（Spec 列为 Out of Scope），
 * 所以这里完全按既有 design token 实现：直角、朱批做主按钮、衬线排标题。
 *
 * 错误提示用黄檗而不是第二种红：按色彩语义，黄檗是「等人判断的地方」，
 * 密码打错正是这个意思；朱批留给「人做的标记」，重墨留给真的停下来了。
 */
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const mode = ref<'login' | 'register'>('login')
const email = ref('')
const password = ref('')
const displayName = ref('')
const error = ref('')
const busy = ref(false)

function switchMode(next: 'login' | 'register') {
  mode.value = next
  error.value = ''
}

async function submit() {
  error.value = ''
  busy.value = true
  try {
    if (mode.value === 'register') {
      await auth.register(email.value, password.value, displayName.value)
    } else {
      await auth.login(email.value, password.value)
    }
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '连不上服务器，稍后再试。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <form class="card" @submit.prevent="submit">
      <div class="brand">
        <span class="seal">魂</span>
        <div class="titles">
          <h1>书魂</h1>
        </div>
      </div>

      <div class="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          :aria-selected="mode === 'login'"
          :class="{ on: mode === 'login' }"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="mode === 'register'"
          :class="{ on: mode === 'register' }"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <label class="field">
        <span>邮箱</span>
        <!-- 这一页只有一件事可做，光标直接放在第一格 -->
        <input v-model="email" type="email" autocomplete="email" required autofocus />
      </label>

      <label v-if="mode === 'register'" class="field">
        <span>称呼<em>可不填</em></span>
        <input v-model="displayName" type="text" maxlength="64" autocomplete="nickname" />
      </label>

      <label class="field">
        <span>密码<em v-if="mode === 'register'">至少 8 位</em></span>
        <input
          v-model="password"
          type="password"
          minlength="8"
          required
          :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
        />
      </label>

      <p v-if="error" class="error" role="alert">{{ error }}</p>

      <button class="submit" type="submit" :disabled="busy">
        {{ busy ? '请稍候' : mode === 'login' ? '登录' : '注册并进入' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  background: var(--paper);
}

.card {
  width: 420px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 36px 38px 30px;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
}

.brand {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.seal {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border: 1.5px solid var(--mark);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 17px;
  color: var(--mark);
}

.titles h1 {
  font-size: 21px;
  line-height: 1.5;
}

.tabs {
  display: flex;
  gap: 22px;
  border-bottom: 1px solid var(--rule);
}

.tabs button {
  padding: 0 0 8px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 13px;
}

.tabs button.on {
  color: var(--text);
  border-bottom-color: var(--mark);
  font-weight: 500;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field > span {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.field em {
  font-style: normal;
  font-size: 11px;
  color: var(--text-faint);
}

.field input {
  height: 34px;
  padding: 0 10px;
  background: var(--paper);
  border: 1px solid var(--edge-strong);
  color: var(--text);
}

.field input:focus {
  border-color: var(--mark);
}

.error {
  margin: 0;
  padding: 9px 12px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
  line-height: 1.7;
}

.submit {
  height: 36px;
  background: var(--mark);
  border: none;
  color: var(--surface);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.04em;
}

.submit:hover:not(:disabled) {
  background: var(--mark-strong);
}
</style>
