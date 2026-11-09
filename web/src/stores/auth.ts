import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import * as authApi from '@/api/auth'
import { readTokens, writeTokens } from '@/api/client'
import type { User } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  // 有 token 就先当作已登录，避免刷新页面时闪一下登录页。
  // 真伪由随后的 restore() 向 /api/auth/me 核实。
  const hasToken = ref(readTokens() !== null)
  const restoring = ref(false)

  const isAuthenticated = computed(() => hasToken.value)

  async function restore(): Promise<void> {
    if (!hasToken.value || user.value) return
    restoring.value = true
    try {
      user.value = await authApi.me()
    } catch {
      // token 过期或被撤销。client 已经清过 localStorage，这里同步掉内存状态。
      hasToken.value = false
      user.value = null
    } finally {
      restoring.value = false
    }
  }

  async function register(email: string, password: string, displayName: string): Promise<void> {
    const tokens = await authApi.register(email, password, displayName)
    writeTokens(tokens)
    hasToken.value = true
    user.value = await authApi.me()
  }

  async function login(email: string, password: string): Promise<void> {
    const tokens = await authApi.login(email, password)
    writeTokens(tokens)
    hasToken.value = true
    user.value = await authApi.me()
  }

  /** 主动登出；token 在用着的时候失效、续期也续不上（router 的 401 处理）也走这里。 */
  function logout(): void {
    writeTokens(null)
    hasToken.value = false
    user.value = null
  }

  return { user, isAuthenticated, restoring, restore, register, login, logout }
})
