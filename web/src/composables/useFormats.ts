import { computed, ref } from 'vue'

import { getFormats, type Capability } from '@/api/formats'

/**
 * 拖放提示文案、文件选择器的 accept、上传前的体积与格式校验，全部由这里驱动。
 *
 * 前端不写死后缀：一份没装 pymupdf 的部署不该继续对外宣称支持 PDF，
 * 而那件事只有服务端知道。
 */
const capability = ref<Capability | null>(null)
let inflight: Promise<void> | null = null

export function useFormats() {
  function load() {
    if (capability.value || inflight) return inflight ?? Promise.resolve()
    inflight = getFormats()
      .then((caps) => {
        capability.value = caps
      })
      .catch(() => {
        // 拿不到就退化成「什么都收」，让服务端去判 —— 总好过把用户挡在门外
        capability.value = { maxUploadMb: 100, formats: [] }
      })
      .finally(() => {
        inflight = null
      })
    return inflight
  }

  const available = computed(() => (capability.value?.formats ?? []).filter((f) => f.available))
  const unavailable = computed(() => (capability.value?.formats ?? []).filter((f) => !f.available))
  const maxUploadMb = computed(() => capability.value?.maxUploadMb ?? 100)
  const accept = computed(() => available.value.flatMap((f) => f.extensions).join(','))

  /** 上传前粗筛。收得下返回空串；真正的判定仍然按魔数在服务端做。 */
  function reject(file: File): string {
    const caps = capability.value
    if (!caps || caps.formats.length === 0) return ''

    if (file.size > caps.maxUploadMb * 1024 * 1024) {
      const mb = (file.size / 1024 / 1024).toFixed(1)
      return `${mb} MB，超过 ${caps.maxUploadMb} MB 的上限。`
    }

    const lowered = file.name.toLowerCase()
    const match = caps.formats.find((f) => f.extensions.some((ext) => lowered.endsWith(ext)))
    if (!match) {
      const known = available.value.map((f) => f.label).join('、')
      return `认不出这是什么格式。目前收这些：${known}。`
    }
    if (!match.available) {
      return `这个部署暂不支持 ${match.label}。换一个格式，或者找管理员装上对应的解析后端。`
    }
    return ''
  }

  /**
   * 后缀是不是某一种书（这个部署装没装它的解析器都算）。拖进来的是整个目录时，
   * 只挑这些出来：目录里的封面图、说明文字不该一个个变成「认不出这是什么格式」。
   */
  function recognizes(name: string): boolean {
    const lowered = name.toLowerCase()
    return (capability.value?.formats ?? []).some((f) =>
      f.extensions.some((ext) => lowered.endsWith(ext))
    )
  }

  return { capability, load, available, unavailable, maxUploadMb, accept, reject, recognizes }
}
