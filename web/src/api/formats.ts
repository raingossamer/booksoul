import { request } from './client'

export interface FormatInfo {
  key: string
  label: string
  extensions: string[]
  available: boolean
  experimental: boolean
  note: string
}

export interface Capability {
  maxUploadMb: number
  formats: FormatInfo[]
}

/** 这个部署到底支持哪些格式——运行时探测的结果，不是前端写死的一张表。 */
export function getFormats(): Promise<Capability> {
  return request<Capability>('/api/formats')
}
