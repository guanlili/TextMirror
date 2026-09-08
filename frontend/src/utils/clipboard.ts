/**
 * 剪贴板 / 富文本 DOM 工具函数（AI 润色结果复制用）
 */

/** 压缩纯文本：去 nbsp、行尾空白与多余空行 */
export function compactPlainText(text: string): string {
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

/** HTML → 纯文本（借助 DOM innerText），并压缩空白 */
export function htmlToPlainText(html: string): string {
  const container = document.createElement('div')
  container.innerHTML = html
  return compactPlainText(container.innerText)
}

/** 旧浏览器兜底：通过选区 + execCommand 复制富文本 */
export function copyRichTextBySelection(html: string, plainText: string): void {
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

/** 压缩富文本 HTML：统一段落/列表间距、移除空段落与孤立 br */
export function compactRichHtml(html: string): string {
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
