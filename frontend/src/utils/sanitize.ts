/**
 * HTML 消毒
 * 文档预览 HTML 由服务端从 DOCX 属性生成，润色结果由 Markdown 渲染，
 * 两者都要经过 v-html。服务端已做属性白名单，这里是浏览器侧的第二层防御。
 */
import DOMPurify from 'dompurify'

/** 文档预览：保留排版标签与内联 style，禁止脚本/事件/外链资源 */
const DOCUMENT_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'span', 'div', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'mark',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'blockquote', 'code', 'pre',
    'table', 'thead', 'tbody', 'tr', 'td', 'th',
  ],
  ALLOWED_ATTR: ['style', 'class', 'title', 'data-issue-idx'],
  ALLOW_DATA_ATTR: false,
}

/** 润色结果：Markdown 渲染产物，额外允许链接 */
const MARKDOWN_CONFIG = {
  ...DOCUMENT_CONFIG,
  ALLOWED_TAGS: [...DOCUMENT_CONFIG.ALLOWED_TAGS, 'a', 'hr', 'del'],
  ALLOWED_ATTR: [...DOCUMENT_CONFIG.ALLOWED_ATTR, 'href', 'target', 'rel'],
  ALLOWED_URI_REGEXP: /^https?:\/\//i,
}

export function sanitizeDocumentHtml(html: string): string {
  if (!html) return ''
  return DOMPurify.sanitize(html, DOCUMENT_CONFIG)
}

export function sanitizeMarkdownHtml(html: string): string {
  if (!html) return ''
  return DOMPurify.sanitize(html, MARKDOWN_CONFIG)
}
