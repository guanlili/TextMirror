/**
 * TextMirror 校对审阅共享纯函数
 * TextProofread.vue / DocumentProofread.vue 共用（标签映射、高亮色、敏感词删除、导出等）
 */

export const proofreadModeHints: Record<string, string> = {
  single: '一个模型检查全文，适合日常审校；可按需要选择审校深度。',
  compare: '2–4 个模型分别检查同一原文，对照共识与分歧；耗时和用量通常更多。',
  collaboration: '语言与一致性角色分工检查，再复核疑点；适合需要查看审校过程的文本。',
}

export const proofreadDomainHints: Record<string, string> = {
  auto: '按文本特征匹配通用、公文或法律规则；不确定时可选自动。',
  general: '适合日常文章、邮件等文本，使用通用用词和表达规范。',
  official: '适合通知、请示、报告等正式文稿，侧重公文用语、称谓和行文规范。',
  legal: '适合合同、协议等法律文书，侧重术语和条款表达；不替代专业法律意见。',
}

export const proofreadDepthHints: Record<string, string> = {
  quick: '只查词库、格式和规则，不调用 AI；适合快速初筛，复杂语病可能漏检。',
  standard: '规则检查 + AI 审校，兼顾速度与覆盖范围，适合日常使用。',
  deep: '加强分析并增加复查，适合重要文稿；耗时和用量通常更高。',
}

/** 敏感词删除时需一并移除的紧邻标点（避免悬空标点） */
const TRAILING_PUNCT = '，。！？；、,'

/** 转义 HTML，避免原文中含有 < > 等字符破坏 DOM */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 问题严重度 → 原文高亮底色 */
export function severityHighlight(severity: string): string {
  switch (severity) {
    case 'error': return '#fee2e2'  // 柔和红
    case 'warning': return '#fef3c7' // 柔和琥珀
    default: return '#dbeafe'        // 柔和蓝
  }
}

/** 问题严重度 → el-tag 颜色类型 */
export function severityColor(severity: string): 'danger' | 'warning' | 'info' | 'success' | 'primary' {
  switch (severity) {
    case 'error': return 'danger'
    case 'warning': return 'warning'
    default: return 'info'
  }
}

/** 问题严重度 → 中文标签 */
export function severityLabel(severity: string): string {
  switch (severity) {
    case 'error': return '错误'
    case 'warning': return '警告'
    default: return '建议'
  }
}

/** 问题类型 → 中文标签 */
export function typeLabel(type: string): string {
  const map: Record<string, string> = {
    typo: '错别字', grammar: '语法', punctuation: '标点',
    style: '表达', sensitive: '敏感词', logic: '逻辑',
  }
  return map[type] || type
}

/**
 * 计算敏感词删除目标：连同紧邻标点，返回实际删除内容与词首锚点（供撤销插回）。
 * 文本中未找到该词时返回 null。
 */
export function computeSensitiveDeletion(
  text: string,
  word: string,
): { target: string; anchor: number } | null {
  const wordIdx = text.indexOf(word)
  if (wordIdx < 0) return null
  const nextChar = text[wordIdx + word.length]
  const target = nextChar && TRAILING_PUNCT.includes(nextChar) ? word + nextChar : word
  return { target, anchor: wordIdx }
}

/** 仅在文本节点中替换（跳过 HTML 标签），替换该文本节点内的全部出现 */
export function replaceTextInHtml(html: string, searchPlain: string, replacementHtml: string): string {
  const search = escapeHtml(searchPlain)
  return html.replace(/(<[^>]*>)|([^<]+)/g, (match: string, tag: string, text: string) => {
    if (tag || !text.includes(search)) return match
    return text.replaceAll(search, replacementHtml)
  })
}

/** 高亮条目（original + 展示属性 + 全局索引） */
export interface HighlightEntry {
  index: number
  original: string
  severity: string
  type: string
  suggestion: string
}

/**
 * 高亮 HTML 构建（两阶段占位替换）：
 * 1. 原文片段（转义后）先在文本节点内替换为互不冲突的占位符——长原文优先，
 *    防止短词拆散长词（「权力」先替换会拆散「权力机关」）与 mark 嵌套
 * 2. 占位符再统一替换为 <mark>（同一问题的多处出现全部高亮）
 */
export function highlightIssues(
  html: string,
  entries: HighlightEntry[],
  markHtml: (entry: HighlightEntry, escapedOriginal: string) => string,
): string {
  const sorted = [...entries]
    .filter(e => e.original)
    .sort((a, b) => b.original.length - a.original.length)
  const tokens = new Map<string, string>()
  for (const entry of sorted) {
    const escaped = escapeHtml(entry.original)
    const token = `\u0000${entry.index}\u0000`
    html = replaceTextInHtml(html, entry.original, token)
    if (!html.includes(token)) continue
    tokens.set(token, markHtml(entry, escaped))
  }
  for (const [token, mark] of tokens) {
    html = html.replaceAll(token, mark)
  }
  return html
}

/** 触发浏览器下载一个纯文本文件 */
export function downloadTextFile(content: string, filename: string): void {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** 校对问题（含前端 UI 状态字段，下划线前缀） */
export interface CompareIssue {
  original: string
  suggestion: string
  severity: string
  type: string
  explanation?: string
  _accepted?: boolean
  _ignored?: boolean
  _deletedText?: string
  _undoAnchor?: number
}
