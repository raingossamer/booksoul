import { request } from './client'
import type { TokenPair, User } from '@/types'

export function register(email: string, password: string, displayName = ''): Promise<TokenPair> {
  return request<TokenPair>('/api/auth/register', {
    method: 'POST',
    body: { email, password, displayName }
  })
}

export function login(email: string, password: string): Promise<TokenPair> {
  return request<TokenPair>('/api/auth/login', { method: 'POST', body: { email, password } })
}

export function me(): Promise<User> {
  return request<User>('/api/auth/me')
}
