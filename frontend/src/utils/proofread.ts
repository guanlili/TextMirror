/**
 * TextMirror 校对审阅共享纯函数
 * TextProofread.vue / DocumentProofread.vue 共用（标签映射、高亮色、敏感词删除、导出等）
 */

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

/** HTML 工具函数：仅在文本节点中替换首个匹配，跳过 HTML 标签 */
export function replaceTextInHtml(html: string, searchPlain: string, replacementHtml: string): string {
  const search = escapeHtml(searchPlain)
  let replaced = false
  return html.replace(/(<[^>]*>)|([^<]+)/g, (match: string, tag: string, text: string) => {
    if (tag || replaced) return match
    if (text && text.includes(search)) {
      replaced = true
      return text.replace(search, replacementHtml)
    }
    return match
  })
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
