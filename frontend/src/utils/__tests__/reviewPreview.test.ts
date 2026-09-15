import { describe, expect, it } from 'vitest'
import { buildReviewPreview } from '../reviewPreview'
import { createReviewPatch, renderReviewText } from '../review'

const issue = { original: '错误', suggestion: '正确', severity: 'error', type: 'typo', start: 0, end: 2 }

describe('位置化修订预览', () => {
  it('只高亮具体出现而不是全文同词', () => {
    const html = buildReviewPreview('错误和错误', [{ ...issue, start: 3, end: 5 }], [])
    expect(html.startsWith('错误和<mark')).toBe(true)
    expect(html.match(/<mark /g)).toHaveLength(1)
  })
  it('前面的长度变化和emoji不会使高亮错位', () => {
    const source = '😀错误和错误'
    const accepted = { ...issue, start: 1, end: 3, suggestion: '更长的建议', _accepted: true }
    const pending = { ...issue, start: 4, end: 6 }
    const patch = createReviewPatch(source, accepted)!
    const html = buildReviewPreview(renderReviewText(source, [patch]), [accepted, pending], [patch])
    expect(html.startsWith('😀更长的建议和<mark')).toBe(true)
    expect(html).toContain('data-issue-idx="1"')
  })
  it('重叠的待处理问题不会标到已经修改的文字', () => {
    expect(buildReviewPreview('建议', [issue], [{ start: 0, end: 2, replacement: '建议' }])).toBe('建议')
  })
  it('HTML和引号只作为文本显示', () => {
    const text = '<script>alert("x")</script>'
    const html = buildReviewPreview(text, [{ ...issue, original: '<script>', start: 0, end: 8, type: '" onmouseover="alert(1)' }], [])
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('title="" onmouseover')
    expect(html).toContain('&lt;script&gt;')
  })
  it('同位置的不同报告只产生一次高亮且非法级别不能注入属性', () => {
    const html = buildReviewPreview('错误', [{ ...issue, severity: 'x" onmouseover="alert(1)' }, issue], [])
    expect(html.match(/<mark /g)).toHaveLength(1)
    expect(html).toContain('class="hl-info"')
  })
})
