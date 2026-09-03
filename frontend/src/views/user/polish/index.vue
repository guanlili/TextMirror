<template>
  <div class="polish-page">
    <div class="polish-layout">
      <!-- ===== 左侧：输入面板 ===== -->
      <div class="panel-left">
        <!-- 工作区引导 -->
        <div class="page-header">
          <div class="step-heading">
            <span class="step-index">01</span>
            <div>
              <h2>选择表达策略</h2>
              <p>告诉 AI 你希望文字呈现怎样的语气</p>
            </div>
          </div>
          <span class="strategy-count">10 种策略</span>
        </div>

        <!-- 敏感词警告（仅在检测到敏感词时显示） -->
        <transition name="fade-slide">
          <div v-if="showSensitiveWarning" class="sensitive-warning">
            <el-icon class="warning-icon"><WarningFilled /></el-icon>
            <span>内容将被发送至AI服务处理，请勿输入密码、身份证号等敏感信息</span>
          </div>
        </transition>

        <!-- 风格选择 -->
        <div class="style-section">
          <div class="section-title">
            <span>润色风格</span>
            <span class="style-selected-tag">{{ currentSelectedStyleName }}</span>
          </div>
          <div class="style-grid">
            <div
              v-for="item in styles"
              :key="item.key"
              class="style-card"
              :class="{ 'is-active': selectedStyle === item.key }"
              @click="selectedStyle = item.key"
            >
              <div class="card-icon">{{ styleIcons[item.key] || '✨' }}</div>
              <div class="card-content">
                <div class="card-name">{{ item.name }}</div>
                <div class="card-desc">{{ item.description }}</div>
              </div>
              <div v-if="selectedStyle === item.key" class="card-check">
                <el-icon><Check /></el-icon>
              </div>
            </div>
          </div>
          <div class="selected-style-preview">
            <span class="preview-dot" />
            <strong>{{ currentSelectedStyleName }}</strong>
            <span>{{ currentSelectedStyleDesc }}</span>
          </div>
        </div>

        <!-- 文本输入 -->
        <div class="input-section">
          <div class="section-title">
            <span class="section-heading"><b>02</b> 输入原文</span>
            <span class="char-count" :class="{ 'is-error': textTooShort || textTooLong }">
              {{ inputText.length }}/5000
            </span>
          </div>
          <div class="textarea-wrapper">
            <el-input
              v-model="inputText"
              type="textarea"
              :rows="7"
              placeholder="请在此粘贴或输入需要润色的文本内容（10-5000字）..."
              resize="none"
              maxlength="5000"
            />
          </div>
        </div>

        <!-- 多模型对比模式 -->
        <div v-if="availableModels.length >= 2" class="compare-section">
          <el-checkbox v-model="compareMode" size="small">多模型对比</el-checkbox>
          <template v-if="compareMode">
            <el-select
              v-model="selectedModelIds"
              multiple
              collapse-tags
              size="small"
              placeholder="选择 2-4 个模型"
              style="flex: 1; min-width: 0;"
            >
              <el-option
                v-for="m in availableModels"
                :key="m.id"
                :label="`${m.name} (${m.model})`"
                :value="m.id"
              />
            </el-select>
            <el-button
              type="primary"
              size="small"
              :loading="comparing"
              :disabled="!canCompare"
              @click="handleCompare"
            >
              {{ comparing ? '对比中...' : '开始对比' }}
            </el-button>
          </template>
        </div>

        <!-- 操作按钮 -->
        <div class="action-bar">
          <el-button
            class="btn-polish"
            type="primary"
            :loading="loading"
            :disabled="!canSubmit || compareMode"
            @click="handlePolish"
          >
            <el-icon v-if="!loading"><MagicStick /></el-icon>
            {{ loading ? '正在润色...' : '一键润色' }}
          </el-button>
          <el-button class="btn-clear" @click="handleClear" :disabled="!inputText">
            <el-icon><Delete /></el-icon>清空
          </el-button>
        </div>
      </div>

      <!-- ===== 右侧：结果面板 ===== -->
      <div class="panel-right">
        <div class="result-panel-title">
          <div>
            <span class="result-kicker">AI OUTPUT</span>
            <h2>{{ compareMode ? '模型对比' : '润色结果' }}</h2>
          </div>
          <span class="result-hint">{{ compareMode ? '同一段文本由多个模型并发润色' : '将生成轻度、标准、深度 3 个版本' }}</span>
        </div>

        <!-- ===== 多模型对比视图 ===== -->
        <template v-if="compareMode">
          <div v-if="!hasCompareResult && !comparing" class="empty-state">
            <div class="empty-illustration">
              <span class="empty-icon">⚖️</span>
            </div>
            <h3>模型对比结果将在这里展示</h3>
            <p>选择 2-4 个模型，点击「开始对比」，同一段文本将由多个模型并发润色</p>
          </div>
          <div v-if="comparing" class="loading-state">
            <div class="loading-animation"><div class="dot-pulse"></div></div>
            <p>多个模型正在并发润色，请稍候...</p>
          </div>
          <div v-if="hasCompareResult" class="compare-list">
            <div
              v-for="item in compareResults"
              :key="item.config_id"
              class="compare-card"
              :class="{ 'is-failed': !item.success }"
            >
              <div class="compare-head">
                <el-tag effect="dark" size="small" type="info">{{ item.config_name }}</el-tag>
                <span class="compare-model">{{ item.model }}</span>
                <span v-if="item.success" class="compare-elapsed">{{ (item.elapsed_ms / 1000).toFixed(1) }}s</span>
              </div>
              <div v-if="item.success" class="card-body markdown-body" v-html="renderMarkdown(item.content)"></div>
              <div v-else class="card-body compare-error">
                <p>调用失败</p>
                <span>{{ item.error }}</span>
              </div>
              <div v-if="item.success" class="compare-foot">
                <el-button type="primary" size="small" text @click="handleCopy(item.content)">
                  <el-icon><CopyDocument /></el-icon>复制
                </el-button>
              </div>
            </div>
          </div>
        </template>

        <!-- ===== 普通润色视图 ===== -->
        <template v-else>
        <!-- 空状态 -->
        <div v-if="!hasResult && !loading" class="empty-state">
          <div class="empty-illustration">
            <span class="empty-icon">📝</span>
          </div>
          <h3>润色结果将在这里展示</h3>
          <p>输入文本并选择风格，点击「一键润色」即可生成三种不同程度的润色版本</p>
        </div>

        <!-- 加载状态（流式期间已开始渲染结果，不显示整屏 loading） -->
        <div v-if="loading && !hasResult" class="loading-state">
          <div class="loading-animation">
            <div class="dot-pulse"></div>
          </div>
          <p>AI 正在为您润色，请稍候...</p>
          <span class="loading-tip">通常需要 10-30 秒</span>
        </div>

        <!-- 结果头部工具栏 -->
        <div v-if="hasResult" class="result-header">
          <div class="result-meta">
            <el-tag effect="dark" size="small" class="meta-tag">{{ currentStyleName }}</el-tag>
            <span class="meta-text">原文 {{ originalText.length || inputText.length }} 字</span>
          </div>
          <div class="result-actions">
            <el-button v-if="streaming" size="small" type="danger" plain @click="streamAbort?.()">
              停止生成
            </el-button>
            <el-button v-else size="small" @click="handleRegenerate" :loading="regenerating">
              <el-icon><Refresh /></el-icon>重新生成
            </el-button>
          </div>
        </div>

        <!-- 三行润色结果 -->
        <div v-if="hasResult" class="result-list">
          <div
            v-for="(ver, idx) in versions"
            :key="idx"
            class="result-card"
            :class="'level-' + ver.level"
          >
            <!-- 卡片头部 -->
            <div class="card-head">
              <div class="card-badge" :class="'badge-' + ver.level">
                <span class="badge-num">{{ idx + 1 }}</span>
              </div>
              <span class="card-label">{{ ver.label }}</span>
              <el-tag
                :type="levelTagType(ver.level)"
                size="small"
                effect="plain"
                round
              >
                {{ levelDesc(ver.level) }}
              </el-tag>
              <el-button
                class="btn-copy"
                type="primary"
                size="small"
                text
                @click="handleCopy(ver.content)"
              >
                <el-icon><CopyDocument /></el-icon>复制
              </el-button>
            </div>
            <!-- 卡片内容（支持 Markdown；流式未开始时显示等待态） -->
            <div v-if="streaming && !ver.content" class="card-body card-body-pending">
              <span class="pending-tip"><span class="dot-pulse"></span>等待生成...</span>
            </div>
            <div v-else class="card-body markdown-body" v-html="renderMarkdown(ver.content)"></div>
          </div>
        </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import {
  getPolishStylesApi,
  getAvailableModelsApi,
  textPolishApi,
  textPolishStreamApi,
  polishCompareApi,
  polishCompareStreamApi,
  type PolishStyle,
  type PolishVersion,
  type ModelCompareItem,
  type AvailableModel,
} from '@/api/polish'

// ---- Markdown 配置 ----
marked.setOptions({
  breaks: true,
  gfm: true,
})

// ---- 敏感关键词列表 ----
const SENSITIVE_KEYWORDS = [
  '密码', '身份证', '银行卡', '手机号', '验证码',
  '信用卡', '社保', '护照', '驾照', '账号密码',
  'password', 'token', 'secret', 'api_key', 'apikey',
]

// ---- 风格图标映射 ----
const styleIcons: Record<string, string> = {
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

const canCompare = computed(() => {
  const len = inputText.value.trim().length
  return len >= 10 && len <= 5000 && selectedModelIds.value.length >= 2
})

// ---- 计算属性 ----
const textTooShort = computed(() => inputText.value.trim().length > 0 && inputText.value.trim().length < 10)
const textTooLong = computed(() => inputText.value.length > 5000)
const canSubmit = computed(() => {
  const len = inputText.value.trim().length
  return len >= 10 && len <= 5000
})
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
    styles.value = [
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
  }

  // 多模型对比：加载已启用模型列表
  try {
    const res = await getAvailableModelsApi()
    availableModels.value = res.models
  } catch { /* 模型列表加载失败时对比功能不可用 */ }

  // 从校对历史「再次润色」带入的原文
  const rerunText = sessionStorage.getItem('tm_rerun_text')
  if (rerunText) {
    inputText.value = rerunText
    sessionStorage.removeItem('tm_rerun_text')
  }
})

// ---- 方法 ----

/** 流式对比核心：返回是否成功 */
async function runCompareStream(text: string, style: string, configIds: number[]): Promise<boolean> {
  const buffers: Record<number, string> = {}
  let gotAny = false
  let failed = false

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
  } catch (e: any) {
    if (e?.name !== 'AbortError' && !gotAny) {
      failed = true
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

/** Markdown 渲染 */
function renderMarkdown(content: string): string {
  if (!content) return ''
  return marked.parse(content) as string
}

/** 流式润色核心：返回是否成功 */
async function runPolishStream(text: string, style: string): Promise<boolean> {
  const order = ['light', 'standard', 'deep']
  const labelMap: Record<string, string> = { light: '轻量润色', standard: '标准润色', deep: '深度润色' }
  const buffers: Record<string, string> = {}
  const doneLevels = new Set<string>()
  let gotAny = false
  let failed = false

  streaming.value = true
  streamAborted.value = false
  const { promise, abort } = textPolishStreamApi({ text, style }, (evt) => {
    if (evt.event === 'meta') {
      currentStyleName.value = evt.style_name || currentStyleName.value
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
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      streamAborted.value = true
    } else {
      // 未收到任何增量则整体失败（回退同步接口）；部分已到则保留已有内容
      if (!gotAny) {
        failed = true
        ElMessage.error(e?.message || '润色失败，请稍后重试')
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
  } catch {
    // 错误已在拦截器中处理
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

function htmlToPlainText(html: string): string {
  const container = document.createElement('div')
  container.innerHTML = html
  return compactPlainText(container.innerText)
}

function copyRichTextBySelection(html: string, plainText: string) {
  const container = document.createElement('div')
  container.style.position = 'fixed'
  container.style.left = '-9999px'
  container.style.top = '0'
  container.style.whiteSpace = 'pre-wrap'
  container.innerHTML = html || plainText
  document.body.appendChild(container)

  const range = document.createRange()
  range.selectNodeContents(container)
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)

  const successful = document.execCommand('copy')
  selection?.removeAllRanges()
  document.body.removeChild(container)

  if (!successful) {
    throw new Error('复制失败')
  }
}

function compactRichHtml(html: string): string {
  const container = document.createElement('div')
  container.innerHTML = html

  container.querySelectorAll('p, h1, h2, h3, h4, h5, h6, ul, ol, blockquote').forEach((el) => {
    const node = el as HTMLElement
    node.style.marginTop = '0'
    node.style.marginBottom = node.tagName === 'LI' ? '0' : '6px'
    node.style.lineHeight = '1.55'
  })

  container.querySelectorAll('li').forEach((el) => {
    const node = el as HTMLElement
    node.style.marginTop = '0'
    node.style.marginBottom = '2px'
    node.style.lineHeight = '1.55'
  })

  container.querySelectorAll('br').forEach((br) => {
    const prev = br.previousSibling
    const next = br.nextSibling
    if ((!prev || !prev.textContent?.trim()) && (!next || !next.textContent?.trim())) {
      br.remove()
    }
  })

  container.querySelectorAll('p').forEach((p) => {
    if (!p.textContent?.trim()) {
      p.remove()
    }
  })

  return container.innerHTML
}

function compactPlainText(text: string): string {
  return text
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .split('\n')
    .map(line => line.trimEnd())
    .join('\n')
    .trim()
}

/** 改动级别标签类型 */
function levelTagType(level: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  switch (level) {
    case 'light': return 'success'
    case 'standard': return 'warning'
    case 'deep': return 'danger'
    default: return 'info'
  }
}

/** 改动级别描述 */
function levelDesc(level: string): string {
  switch (level) {
    case 'light': return '改动10~30%'
    case 'standard': return '改动40~60%'
    case 'deep': return '改动70~90%'
    default: return ''
  }
}
</script>

<style scoped lang="scss">
/* ===== 整体布局 ===== */
.polish-page {
  height: calc(100vh - 64px);
  overflow: hidden;
}

.polish-layout {
  display: flex;
  height: 100%;
  gap: 0;
}

/* ===== 左侧面板 ===== */
.panel-left {
  flex: 0 0 60%;
  max-width: 60%;
  min-width: 550px;
  padding: 24px;
  background: var(--surface);
  border-right: 1px solid var(--surface-border);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.page-header {
  .header-title {
    display: flex;
    align-items: center;
    gap: 8px;

    .title-icon {
      font-size: 22px;
    }

    h2 {
      font-size: 20px;
      font-weight: 700;
      color: #1a1a2e;
      margin: 0;
      letter-spacing: -0.5px;
    }
  }

  .header-desc {
    margin: 6px 0 0;
    font-size: 13px;
    color: #9ca3af;
  }
}

/* 敏感词警告 */
.sensitive-warning {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: linear-gradient(135deg, #fef3cd 0%, #fff9e6 100%);
  border: 1px solid #fde68a;
  border-radius: 8px;
  font-size: 12px;
  color: #92400e;

  .warning-icon {
    color: #f59e0b;
    font-size: 16px;
    flex-shrink: 0;
  }
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.3s ease;
}
.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* 风格选择 */
.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);

  .style-selected-tag {
    font-size: 12px;
    font-weight: 500;
    color: #0056b3;
    background: #eff6ff;
    padding: 2px 8px;
    border-radius: 4px;
  }

  .char-count {
    font-weight: 400;
    color: #9ca3af;
    font-size: 12px;

    &.is-error {
      color: #ef4444;
      font-weight: 500;
    }
  }
}

.style-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.style-card {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border: 1.5px solid #e5e7eb;
  border-radius: 10px;
  cursor: pointer;
  background: var(--surface);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  user-select: none;

  .card-icon {
    font-size: 22px;
    flex-shrink: 0;
    margin-top: 2px;
  }

  .card-content {
    flex: 1;
    min-width: 0;

    .card-name {
      font-size: 14px;
      font-weight: 600;
      color: #1f2937;
      margin-bottom: 4px;
      line-height: 1.3;
    }

    .card-desc {
      font-size: 12px;
      color: #9ca3af;
      line-height: 1.5;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
  }

  .card-check {
    position: absolute;
    top: 10px;
    right: 10px;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: #0056b3;
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
  }

  &:hover {
    border-color: #93c5fd;
    background: #f8faff;
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(0, 86, 179, 0.06);
  }

  &.is-active {
    border-color: #0056b3;
    background: linear-gradient(135deg, #eff6ff 0%, #ffffff 100%);
    box-shadow: 0 2px 10px rgba(0, 86, 179, 0.12);

    .card-name {
      color: #0056b3;
    }

    .card-desc {
      color: #6b7280;
    }
  }
}

/* 文本输入 */
.input-section {
  display: flex;
  flex-direction: column;
}

.textarea-wrapper {
  display: flex;
  flex-direction: column;

  :deep(.el-textarea) {
    display: flex;
    flex-direction: column;
  }

  :deep(.el-textarea__inner) {
    font-size: 14px;
    line-height: 1.75;
    border-radius: 10px;
    border: 1.5px solid #e5e7eb;
    padding: 14px 16px;
    resize: none;
    transition: border-color 0.2s;

    &:focus {
      border-color: #0056b3;
      box-shadow: 0 0 0 3px rgba(0, 86, 179, 0.06);
    }

    &::placeholder {
      color: #c4c9d4;
    }
  }
}

/* 操作按钮 */
.action-bar {
  display: flex;
  gap: 10px;

  .btn-polish {
    flex: 1;
    height: 42px;
    border-radius: 10px;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 1px;
    background: linear-gradient(135deg, #0056b3 0%, #0077cc 100%);
    border: none;
    box-shadow: 0 4px 12px rgba(0, 86, 179, 0.25);

    &:hover:not(:disabled) {
      transform: translateY(-1px);
      box-shadow: 0 6px 16px rgba(0, 86, 179, 0.35);
    }

    &:active:not(:disabled) {
      transform: translateY(0);
    }
  }

  .btn-clear {
    height: 42px;
    border-radius: 10px;
    border: 1.5px solid #e5e7eb;
    color: #6b7280;
  }
}

/* ===== 右侧面板 ===== */
.panel-right {
  flex: 1;
  padding: 24px;
  background: #f8f9fb;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* 空状态 */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;

  .empty-illustration {
    width: 80px;
    height: 80px;
    border-radius: 50%;
    background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 16px;

    .empty-icon {
      font-size: 36px;
    }
  }

  h3 {
    font-size: 16px;
    font-weight: 600;
    color: var(--color-text);
    margin: 0 0 8px;
  }

  p {
    font-size: 13px;
    color: #9ca3af;
    max-width: 280px;
    line-height: 1.6;
  }
}

/* 加载状态 */
.loading-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;

  p {
    margin-top: 20px;
    font-size: 15px;
    color: var(--color-text);
    font-weight: 500;
  }

  .loading-tip {
    margin-top: 8px;
    font-size: 12px;
    color: #9ca3af;
  }
}

.dot-pulse {
  position: relative;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #0056b3;
  animation: dot-pulse 1.5s infinite linear;

  &::before,
  &::after {
    content: '';
    position: absolute;
    top: 0;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #0056b3;
  }

  &::before {
    left: -20px;
    animation: dot-pulse 1.5s infinite linear;
    animation-delay: -0.5s;
  }

  &::after {
    left: 20px;
    animation: dot-pulse 1.5s infinite linear;
    animation-delay: 0.5s;
  }
}

@keyframes dot-pulse {
  0%, 60%, 100% {
    opacity: 0.3;
    transform: scale(0.8);
  }
  30% {
    opacity: 1;
    transform: scale(1.2);
  }
}

/* 结果头部 */
.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;

  .result-meta {
    display: flex;
    align-items: center;
    gap: 10px;

    .meta-tag {
      background: linear-gradient(135deg, #0056b3 0%, #0077cc 100%);
      border: none;
      border-radius: 6px;
    }

    .meta-text {
      font-size: 12px;
      color: #9ca3af;
    }
  }
}

/* 结果卡片列表 */
.result-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  flex: 1;
}

.result-card {
  background: var(--surface);
  border-radius: 12px;
  border: 1px solid #f0f0f0;
  overflow: hidden;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);

  &:hover {
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
    border-color: #e0e7ff;
    transform: translateY(-1px);
  }

  &.level-light {
    border-left: 3px solid #10b981;
  }

  &.level-standard {
    border-left: 3px solid #f59e0b;
  }

  &.level-deep {
    border-left: 3px solid #ef4444;
  }
}

.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid #f7f7f8;
  background: #fafbfc;

  .card-badge {
    width: 24px;
    height: 24px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;

    .badge-num {
      font-size: 12px;
      font-weight: 700;
      color: #fff;
    }

    &.badge-light {
      background: linear-gradient(135deg, #10b981, #34d399);
    }

    &.badge-standard {
      background: linear-gradient(135deg, #f59e0b, #fbbf24);
    }

    &.badge-deep {
      background: linear-gradient(135deg, #ef4444, #f87171);
    }
  }

  .card-label {
    font-size: 14px;
    font-weight: 600;
    color: #1f2937;
  }

  .btn-copy {
    margin-left: auto;
    font-size: 12px;
  }
}

.card-body-pending {
  display: flex;
  align-items: center;
  min-height: 48px;

  .pending-tip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--color-text-secondary);
    font-size: 12px;

    .dot-pulse {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #2d73dd;
      animation: dotPulse 1s infinite ease-in-out;
    }
  }
}

/* ===== 多模型对比 ===== */
.compare-section {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px dashed #dbe4ef;
  border-radius: 10px;
  background: var(--surface-soft);
}

.compare-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}

.compare-card {
  border: 1px solid var(--surface-border);
  border-radius: 12px;
  background: var(--surface);
  overflow: hidden;

  &.is-failed {
    border-color: #f3c1c1;
    background: #fdf6f6;
  }
}

.compare-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #f0f4f9;

  .compare-model {
    color: var(--color-text-secondary);
    font-size: 12px;
    font-family: monospace;
  }

  .compare-elapsed {
    margin-left: auto;
    color: #2d73dd;
    font-size: 12px;
    font-weight: 600;
  }
}

.compare-error {
  p { margin: 0 0 4px; color: #c04040; font-weight: 600; font-size: 13px; }
  span { color: #a05a5a; font-size: 12px; word-break: break-all; }
}

.compare-foot {
  padding: 6px 10px;
  border-top: 1px solid #f0f4f9;
  text-align: right;
}

.card-body {
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text);
  max-height: calc((100vh - 240px) / 3 - 60px);
  overflow-y: auto;

  // Markdown 渲染样式
  :deep(p) {
    margin: 0 0 8px;

    &:last-child {
      margin-bottom: 0;
    }
  }

  :deep(ul), :deep(ol) {
    margin: 4px 0 8px;
    padding-left: 20px;
  }

  :deep(li) {
    margin-bottom: 4px;
  }

  :deep(strong) {
    font-weight: 600;
    color: #111827;
  }

  :deep(code) {
    background: #f3f4f6;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 13px;
    color: #e11d48;
  }

  :deep(blockquote) {
    margin: 8px 0;
    padding: 8px 12px;
    border-left: 3px solid #e5e7eb;
    background: #f9fafb;
    color: #6b7280;
  }

  :deep(h1), :deep(h2), :deep(h3), :deep(h4) {
    margin: 12px 0 6px;
    font-weight: 600;
    color: #111827;
  }

  :deep(h1) { font-size: 18px; }
  :deep(h2) { font-size: 16px; }
  :deep(h3) { font-size: 15px; }
  :deep(h4) { font-size: 14px; }
}

/* ===== 移动端响应式 ===== */
@media (max-width: 768px) {
  .polish-page {
    height: auto;
    overflow: auto;
  }

  .polish-layout {
    flex-direction: column;
    height: auto;
  }

  .panel-left {
    flex: none;
    max-width: 100%;
    min-width: 0;
    width: 100%;
    padding: 16px;
    border-right: none;
    border-bottom: 1px solid #f0f0f0;
    gap: 14px;
  }

  .page-header {
    .header-title h2 {
      font-size: 18px;
    }
    .header-desc {
      display: none;
    }
  }

  /* 移动端风格卡片：简化为紧凑 chip，隐藏说明 */
  .style-grid {
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    gap: 8px;
  }

  .style-card {
    padding: 8px 10px;
    gap: 6px;

    .card-icon {
      font-size: 18px;
    }

    .card-content {
      .card-name {
        font-size: 13px;
        margin-bottom: 0;
      }
      .card-desc {
        display: none;
      }
    }

    .card-check {
      width: 16px;
      height: 16px;
      top: 6px;
      right: 6px;
      font-size: 10px;
    }
  }

  .panel-right {
    padding: 16px;
    min-height: 300px;
  }

  .result-list {
    gap: 10px;
  }

  .card-body {
    max-height: none;
  }

  .action-bar {
    .btn-polish {
      height: 40px;
      font-size: 14px;
    }
    .btn-clear {
      height: 40px;
    }
  }
}

/* ===== 2026 工作台视觉升级 ===== */
.polish-page {
  height: calc(100vh - 124px);
  min-height: 620px;
  overflow: hidden;
}

.polish-layout {
  gap: 20px;
  max-width: 1520px;
  margin: 0 auto;
}

.panel-left,
.panel-right {
  border: 1px solid var(--surface-border);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(24, 54, 91, 0.055);
}

.panel-left {
  flex-basis: 58%;
  max-width: 58%;
  min-width: 500px;
  padding: 22px;
  gap: 18px;
  border-right: 1px solid #e5ebf3;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;

  .step-heading {
    display: flex;
    align-items: center;
    gap: 11px;
  }

  .step-index {
    width: 34px;
    height: 34px;
    display: grid;
    place-items: center;
    border-radius: 10px;
    color: #2368d4;
    background: #edf4ff;
    font-size: 12px;
    font-weight: 750;
  }

  h2 {
    margin: 0 0 3px;
    color: var(--color-text);
    font-size: 16px;
    font-weight: 650;
  }

  p {
    margin: 0;
    color: var(--color-text-secondary);
    font-size: 11px;
  }

  .strategy-count {
    padding: 5px 9px;
    border: 1px solid var(--surface-border);
    border-radius: 999px;
    color: var(--color-text-secondary);
    background: var(--surface-soft);
    font-size: 11px;
  }
}

.style-section { margin-top: -2px; }
.style-section > .section-title { display: none; }

.style-grid {
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.style-card {
  min-width: 0;
  min-height: 66px;
  padding: 10px 7px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: 1px solid var(--surface-border);
  border-radius: 11px;
  background: var(--surface-soft);

  .card-icon {
    margin: 0;
    font-size: 19px;
    line-height: 1;
    filter: saturate(.82);
  }

  .card-content {
    flex: 0;
    text-align: center;

    .card-name {
      margin: 0;
      color: var(--color-text-secondary);
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
    }

    .card-desc { display: none; }
  }

  .card-check {
    width: 15px;
    height: 15px;
    top: 5px;
    right: 5px;
    font-size: 9px;
    background: #2d73dd;
  }

  &:hover {
    border-color: #9fbff1;
    background: #f5f9ff;
    transform: translateY(-1px);
    box-shadow: 0 5px 14px rgba(45, 115, 221, .09);
  }

  &.is-active {
    border-color: #2d73dd;
    background: linear-gradient(145deg, #eef5ff, #f9fbff);
    box-shadow: 0 0 0 2px rgba(45, 115, 221, .09);

    .card-name { color: #1e5fbd; }
  }
}

.selected-style-preview {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  margin-top: 9px;
  padding: 7px 10px;
  border-radius: 9px;
  color: var(--color-text-secondary);
  background: var(--surface-soft);
  font-size: 11px;

  .preview-dot { width: 6px; height: 6px; border-radius: 50%; background: #3377dc; }
  strong { color: var(--color-primary); font-weight: 650; }
  span:last-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}

.section-title {
  margin-bottom: 9px;

  .section-heading {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--color-text);
    font-size: 14px;

    b {
      width: 25px;
      height: 25px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      color: #2368d4;
      background: #edf4ff;
      font-size: 10px;
    }
  }
}

.textarea-wrapper :deep(.el-textarea__inner) {
  min-height: 184px !important;
  padding: 15px 16px;
  border: 1px solid #dfe6ef;
  border-radius: 12px;
  color: #25344a;
  background: var(--surface-soft);
  box-shadow: none;

  &:focus {
    border-color: #4e86db;
    background: var(--surface);
    box-shadow: 0 0 0 3px rgba(45, 115, 221, .08);
  }
}

.action-bar {
  margin-top: auto;

  .btn-polish {
    height: 44px;
    border-radius: 11px;
    background: linear-gradient(135deg, #2469d3, #3984ed);
    box-shadow: 0 7px 18px rgba(39, 105, 207, .22);
  }

  .btn-clear { height: 44px; min-width: 88px; border-radius: 11px; }
}

.panel-right {
  padding: 22px;
  background: var(--surface-soft);
}

.result-panel-title {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding: 0 2px 17px;
  border-bottom: 1px solid #e7ecf3;

  .result-kicker { color: #3975d4; font-size: 9px; font-weight: 750; letter-spacing: 1.4px; }
  h2 { margin: 3px 0 0; color: var(--color-text); font-size: 16px; font-weight: 650; }
  .result-hint { color: #97a3b2; font-size: 10px; white-space: nowrap; }
}

.empty-state {
  .empty-illustration {
    width: 88px;
    height: 88px;
    position: relative;
    background: linear-gradient(145deg, #eaf2ff, #f7faff);
    box-shadow: inset 0 0 0 1px #e1eafb, 0 12px 28px rgba(48, 107, 196, .09);

    &::after {
      content: '';
      position: absolute;
      inset: -12px;
      border: 1px dashed #dbe6f5;
      border-radius: 50%;
    }
  }

  h3 { margin-top: 9px; color: #304057; }
  p { color: #929eae; }
}

.result-header { margin: 16px 0 12px; }
.result-list { gap: 12px; }
.result-card { border-color: #e3e9f1; border-radius: 12px; box-shadow: 0 3px 12px rgba(31, 55, 84, .04); }
.card-head { background: var(--surface-soft); }

@media (max-width: 1180px) {
  .style-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .panel-left { min-width: 450px; }
  .result-panel-title .result-hint { display: none; }
}

@media (max-width: 900px) {
  .polish-page { height: auto; min-height: 0; overflow: visible; }
  .polish-layout { flex-direction: column; height: auto; }
  .panel-left, .panel-right { width: 100%; max-width: 100%; min-width: 0; padding: 16px; }
  .panel-right { min-height: 430px; }
  .style-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }
  .card-body { max-height: none; }
}

@media (max-width: 600px) {
  .style-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .page-header .strategy-count, .selected-style-preview span:last-child { display: none; }
  .textarea-wrapper :deep(.el-textarea__inner) { min-height: 220px !important; }
}
</style>
