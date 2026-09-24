import { marked } from 'marked'
import { sanitizeMarkdownHtml } from '@/utils/sanitize'

marked.setOptions({
  breaks: true,
  gfm: true,
})

/** 渲染 Markdown 为经消毒的 HTML（供 v-html 使用）。空串原样返回。 */
export function renderMarkdown(content: string): string {
  if (!content) return ''
  return sanitizeMarkdownHtml(marked.parse(content) as string)
}
