import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown } from '@/utils/markdown'
import { htmlToPlainText, copyRichTextBySelection, compactRichHtml } from '@/utils/clipboard'
import {
  getPolishStylesApi,
  getAvailableModelsCached,
  textPolishApi,
  textPolishStreamApi,
  polishCompareApi,
  polishCompareStreamApi,
  type PolishStyle,
  type PolishVersion,
  type ModelCompareItem,
  type AvailableModel,
} from '@/api/polish'

const POLISH_TEXT_MIN_LEN = 10
const POLISH_TEXT_MAX_LEN = 5000

// ---- 敏感关键词列表 ----
const SENSITIVE_KEYWORDS = [
  '密码', '身份证', '银行卡', '手机号', '验证码',
  '信用卡', '社保', '护照', '驾照', '账号密码',
  'password', 'token', 'secret', 'api_key', 'apikey',
]

// ---- 风格图标映射 ----
export const styleIcons: Record<string, string> = {
  formal: '📋',
  friendly: '😊',
  plain: '💬',
  concise: '⚡',
  evidence: '📊',
  strategic: '🏔️',
  practical: '🎯',
  firm: '🤝',
  gentle: '🌸',
  action: '🚀',
}

// 风格接口失败时的兜底列表
const FALLBACK_STYLES: PolishStyle[] = [
  { key: 'formal', name: '正式规范', description: '标准公文语体，结构完整、用词严谨' },
  { key: 'friendly', name: '亲和自然', description: '像面对面聊天，去掉官腔，拉近距离' },
  { key: 'plain', name: '通俗易懂', description: '用大白话解释专业内容，降低理解门槛' },
  { key: 'concise', name: '极简干练', description: '只保留结论和关键信息，30秒看完' },
  { key: 'evidence', name: '有理有据', description: '每个观点都有数据或事实支撑' },
  { key: 'strategic', name: '高屋建瓴', description: '从战略视角出发，体现格局和高度' },
  { key: 'practical', name: '落地务实', description: '谁来做、怎么做、何时完成' },
  { key: 'firm', name: '温和坚定', description: '态度明确但不带攻击性' },
  { key: 'gentle', name: '委婉缓冲', description: '先肯定再提问题，降低抵触情绪' },
  { key: 'action', name: '推进行动', description: '结尾必带下一步动作和时间节点' },
]

/** 改动级别标签类型 */
export function levelTagType(level: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  switch (level) {
    case 'light': return 'success'
    case 'standard': return 'warning'
    case 'deep': return 'danger'
    default: return 'info'
  }
}

/** 改动级别描述 */
export function levelDesc(level: string): string {
  switch (level) {
    case 'light': return '改动10~30%'
    case 'standard': return '改动40~60%'
    case 'deep': return '改动70~90%'
    default: return ''
  }
}

/**
 * 润色页状态与逻辑：风格/输入/三档流式润色/多模型对比/复制。
 * 模板与样式保持不变，本组合式返回的接口名与页面绑定完全一致。
 */
export function usePolish() {
  // ---- 状态 ----
  const styles = ref<PolishStyle[]>([])
  const selectedStyle = ref('formal')
  const inputText = ref('')
  const loading = ref(false)
  const regenerating = ref(false)
  const versions = ref<PolishVersion[]>([])
  const originalText = ref('')
  const currentStyleName = ref('')
  const showSensitiveWarning = ref(false)
  const streaming = ref(false)
  const streamAborted = ref(false)
  let streamAbort: (() => void) | null = null

  // ---- 多模型对比 ----
  const compareMode = ref(false)
  const availableModels = ref<AvailableModel[]>([])
  const selectedModelIds = ref<number[]>([])
  const compareResults = ref<ModelCompareItem[]>([])
  const comparing = ref(false)
  const hasCompareResult = computed(() => compareResults.value.length > 0)
  let compareAbort: (() => void) | null = null

  function stopStreaming() {
    streamAbort?.()
  }

  onUnmounted(() => {
    stopStreaming()
    compareAbort?.()
  })

  const canCompare = computed(() => {
    const len = inputText.value.trim().length
    return len >= POLISH_TEXT_MIN_LEN && len <= POLISH_TEXT_MAX_LEN && selectedModelIds.value.length >= 2
  })

  // ---- 计算属性 ----
  const trimmedLen = computed(() => inputText.value.trim().length)
  const textTooShort = computed(() => trimmedLen.value > 0 && trimmedLen.value < POLISH_TEXT_MIN_LEN)
  const textTooLong = computed(() => inputText.value.length > POLISH_TEXT_MAX_LEN)
  const canSubmit = computed(() => trimmedLen.value >= POLISH_TEXT_MIN_LEN && trimmedLen.value <= POLISH_TEXT_MAX_LEN)
  const hasResult = computed(() => versions.value.length > 0)
  const currentSelectedStyleName = computed(() => {
    const found = styles.value.find(s => s.key === selectedStyle.value)
    return found ? found.name : '正式规范'
  })

  const currentSelectedStyleDesc = computed(() => {
    const found = styles.value.find(s => s.key === selectedStyle.value)
    return found ? found.description : '标准公文语体，结构完整、用词严谨、格式规范'
  })

  // ---- 敏感词检测 ----
  watch(inputText, (val) => {
    const lower = val.toLowerCase()
    showSensitiveWarning.value = SENSITIVE_KEYWORDS.some(kw => lower.includes(kw.toLowerCase()))
  })

  // ---- 生命周期 ----
  onMounted(async () => {
    try {
      const res = await getPolishStylesApi()
      styles.value = res.styles
    } catch {
      styles.value = FALLBACK_STYLES
    }

    // 多模型对比：加载已启用模型列表
    try {
      const res = await getAvailableModelsCached()
      availableModels.value = res.models
    } catch {
      ElMessage.warning('可用模型列表加载失败，多模型对比功能暂不可用')
    }

    // 从校对历史「再次润色」带入的原文
    const rerunText = sessionStorage.getItem('tm_rerun_text')
    if (rerunText) {
      inputText.value = rerunText
      sessionStorage.removeItem('tm_rerun_text')
    }
  })

  /** 流式对比核心：返回是否成功 */
  async function runCompareStream(text: string, style: string, configIds: number[]): Promise<boolean> {
    const buffers: Record<number, string> = {}
    let gotAny = false
    let failed = false
    let gotEnd = false

    const { promise, abort } = polishCompareStreamApi({ text, style, config_ids: configIds }, (evt) => {
      if (evt.event === 'meta' && evt.models) {
        // meta 到达即建立各模型卡片（等待态）
        compareResults.value = evt.models.map(m => ({
          config_id: m.config_id,
          config_name: m.config_name,
          model: m.model,
          content: '',
          success: true,
          elapsed_ms: 0,
        }))
        return
      }
      if (evt.event === 'end') {
        gotEnd = true
        return
      }
      const cid = evt.config_id
      if (cid === undefined) return
      const target = compareResults.value.find(r => r.config_id === cid)
      if (!target) return

      if (evt.event === 'delta') {
        gotAny = true
        buffers[cid] = (buffers[cid] || '') + (evt.content || '')
        target.content = buffers[cid]
      } else if (evt.event === 'done') {
        if (evt.content) target.content = evt.content
        if (evt.elapsed_ms) target.elapsed_ms = evt.elapsed_ms
        if (evt.config_name) target.config_name = evt.config_name
        if (evt.model) target.model = evt.model
      } else if (evt.event === 'error') {
        target.success = false
        target.error = evt.message || '调用失败'
      }
    })
    compareAbort = abort

    try {
      await promise
      if (gotAny && !gotEnd) {
        ElMessage.warning('连接中断，部分模型结果可能不完整')
      }
    } catch (e: unknown) {
      // 卸载中断向上抛出，调用方吞掉并跳过同步回退
      if ((e as Error)?.name === 'AbortError') throw e
      if (!gotAny) {
        failed = true
      } else {
        ElMessage.warning('连接中断，部分模型结果可能不完整')
      }
    } finally {
      compareAbort = null
    }
    return !failed && gotAny
  }

  /** 执行多模型对比 */
  async function handleCompare() {
    if (!canCompare.value) return
    comparing.value = true
    compareResults.value = []
    try {
      const ok = await runCompareStream(inputText.value, selectedStyle.value, selectedModelIds.value)
      if (!ok) {
        // 流式不可用时回退同步对比
        const res = await polishCompareApi({
          text: inputText.value,
          style: selectedStyle.value,
          config_ids: selectedModelIds.value,
        })
        compareResults.value = res.results
      }
      const okCount = compareResults.value.filter(r => r.success).length
      if (okCount === compareResults.value.length && okCount > 0) ElMessage.success(`${okCount} 个模型对比完成`)
      else if (okCount > 0) ElMessage.warning(`${okCount}/${compareResults.value.length} 个模型成功，失败项请查看卡片说明`)
      else ElMessage.error('所有模型调用失败，请检查管理后台的模型配置')
    } catch {
      // 错误已在拦截器中处理
    } finally {
      comparing.value = false
    }
  }

  /** 流式润色核心：返回是否成功 */
  async function runPolishStream(text: string, style: string): Promise<boolean> {
    const order = ['light', 'standard', 'deep']
    const labelMap: Record<string, string> = { light: '轻量润色', standard: '标准润色', deep: '深度润色' }
    const buffers: Record<string, string> = {}
    const doneLevels = new Set<string>()
    let gotAny = false
    let failed = false
    let gotEnd = false

    streaming.value = true
    streamAborted.value = false
    const { promise, abort } = textPolishStreamApi({ text, style }, (evt) => {
      if (evt.event === 'meta') {
        currentStyleName.value = evt.style_name || currentStyleName.value
        return
      }
      if (evt.event === 'end') {
        gotEnd = true
        return
      }
      const lv = evt.level || ''
      if (evt.event === 'delta' && lv) {
        gotAny = true
        buffers[lv] = (buffers[lv] || '') + (evt.content || '')
        // 首个增量时建立三张卡片（未开始的显示等待态）
        if (versions.value.length === 0) {
          versions.value = order.map(level => ({
            label: labelMap[level],
            level,
            content: level === lv ? buffers[lv] : '',
          }))
        } else {
          const target = versions.value.find(v => v.level === lv)
          if (target) target.content = buffers[lv]
        }
      } else if (evt.event === 'done' && lv) {
        doneLevels.add(lv)
        const target = versions.value.find(v => v.level === lv)
        if (target && evt.content) target.content = evt.content
      } else if (evt.event === 'error' && lv) {
        const target = versions.value.find(v => v.level === lv)
        if (target) target.content = `（${labelMap[lv] || lv}生成失败，请重试）`
      } else if (evt.event === 'fatal') {
        failed = true
        ElMessage.error(evt.message || '润色服务暂时不可用，请稍后重试')
      }
    })
    streamAbort = abort

    try {
      await promise
      // 流正常关闭但没收到 end 事件 = 连接被中途截断，已收到的内容可能不完整
      if (gotAny && !gotEnd && !streamAborted.value) {
        ElMessage.warning('连接中断，结果可能不完整，建议重新生成')
      }
    } catch (e: unknown) {
      if ((e as Error)?.name === 'AbortError') {
        streamAborted.value = true
        // 主动停止或卸载不是流式失败，交由调用方结束操作，禁止同步回退。
        throw e
      } else {
        // 未收到任何增量则整体失败（回退同步接口）；部分已到则保留已有内容
        if (!gotAny) {
          failed = true
          ElMessage.error((e as Error)?.message || '润色失败，请稍后重试')
        } else {
          ElMessage.warning('连接中断，结果可能不完整，建议重新生成')
        }
      }
    } finally {
      streaming.value = false
      streamAbort = null
    }
    return !failed && gotAny
  }

  /** 执行润色 */
  async function handlePolish() {
    if (!canSubmit.value) return
    loading.value = true
    versions.value = []
    try {
      const ok = await runPolishStream(inputText.value, selectedStyle.value)
      if (!ok) {
        // 流式不可用时回退同步接口
        const res = await textPolishApi({ text: inputText.value, style: selectedStyle.value })
        versions.value = res.versions
        currentStyleName.value = res.style_name
        originalText.value = inputText.value
      } else {
        originalText.value = inputText.value
        if (!streamAborted.value) ElMessage.success('润色完成')
      }
    } catch (e: unknown) {
      // 取消后保留部分结果对应的原文，仍可重新生成；不提示成功或回退。
      if ((e as Error)?.name === 'AbortError' && versions.value.length > 0) {
        originalText.value = inputText.value
      }
      // 其他错误已在拦截器中处理
    } finally {
      loading.value = false
    }
  }

  /** 清空输入 */
  function handleClear() {
    inputText.value = ''
    versions.value = []
    compareResults.value = []
  }

  /** 重新生成 */
  async function handleRegenerate() {
    regenerating.value = true
    loading.value = true
    versions.value = []
    try {
      const ok = await runPolishStream(originalText.value, selectedStyle.value)
      if (!ok) {
        const res = await textPolishApi({ text: originalText.value, style: selectedStyle.value })
        versions.value = res.versions
        currentStyleName.value = res.style_name
      } else if (!streamAborted.value) {
        ElMessage.success('已重新生成')
      }
    } catch {
      // 错误已在拦截器中处理
    } finally {
      regenerating.value = false
      loading.value = false
    }
  }

  /** 复制内容 */
  async function handleCopy(content: string) {
    const html = compactRichHtml(renderMarkdown(content))
    const plainText = htmlToPlainText(html) || content

    try {
      if (navigator.clipboard && window.ClipboardItem) {
        await navigator.clipboard.write([
          new ClipboardItem({
            'text/html': new Blob([html], { type: 'text/html' }),
            'text/plain': new Blob([plainText], { type: 'text/plain' }),
          }),
        ])
      } else {
        copyRichTextBySelection(html, plainText)
      }
      ElMessage.success('已复制带格式文本，可直接粘贴到飞书')
    } catch {
      try {
        copyRichTextBySelection(html, plainText)
        ElMessage.success('已复制带格式文本，可直接粘贴到飞书')
      } catch {
        await navigator.clipboard.writeText(plainText)
        ElMessage.success('已复制纯文本到剪贴板')
      }
    }
  }

  return {
    // 常量/纯函数
    styleIcons,
    levelTagType,
    levelDesc,
    renderMarkdown,
    // 状态
    styles,
    selectedStyle,
    inputText,
    loading,
    regenerating,
    versions,
    originalText,
    currentStyleName,
    showSensitiveWarning,
    streaming,
    compareMode,
    availableModels,
    selectedModelIds,
    compareResults,
    comparing,
    // 计算属性
    hasCompareResult,
    canCompare,
    textTooShort,
    textTooLong,
    canSubmit,
    hasResult,
    currentSelectedStyleName,
    currentSelectedStyleDesc,
    // 方法
    stopStreaming,
    handleCompare,
    handlePolish,
    handleClear,
    handleRegenerate,
    handleCopy,
  }
}
