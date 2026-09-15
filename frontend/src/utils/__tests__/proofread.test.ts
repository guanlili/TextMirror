import { describe, expect, it } from 'vitest'
import { highlightIssues, replaceTextInHtml, proofreadModeHints, proofreadDomainHints, proofreadDepthHints } from '../proofread'

describe('proofreading setting hints', () => {
  it.each([
    ['single', proofreadModeHints], ['compare', proofreadModeHints], ['collaboration', proofreadModeHints],
    ['auto', proofreadDomainHints], ['general', proofreadDomainHints], ['official', proofreadDomainHints], ['legal', proofreadDomainHints],
    ['quick', proofreadDepthHints], ['standard', proofreadDepthHints], ['deep', proofreadDepthHints],
  ] as const)('provides a brief explanation for %s', (value, hints) => {
    expect(hints[value].length).toBeGreaterThan(10)
    expect(hints[value].length).toBeLessThanOrEqual(80)
  })
  it('explains scope and tradeoffs without guaranteeing correctness', () => {
    expect(proofreadDepthHints.quick).toContain('不调用 AI')
    expect(proofreadDepthHints.deep).toContain('耗时和用量')
    expect(proofreadDomainHints.legal).toContain('不替代专业法律意见')
    expect(proofreadModeHints.compare).toContain('分别检查同一原文')
    expect(proofreadModeHints.collaboration).toContain('分工检查')
  })
})

describe('highlightIssues', () => {
  const mark = (e: { severity: string; type: string; suggestion: string }, escaped: string) =>
    `<mark class="hl-${e.severity}">${escaped}</mark>`

  it('同一错词多处出现全部高亮', () => {
    const html = '帐号错了，帐号又错了'
    const out = highlightIssues(html, [
      { index: 0, original: '帐号', severity: 'error', type: 'typo', suggestion: '账号' },
    ], mark)
    expect(out).toBe('<mark class="hl-error">帐号</mark>错了，<mark class="hl-error">帐号</mark>又错了')
  })

  it('长原文优先：短词不拆散长词（权力 vs 权力机关）', () => {
    const html = '权力机关行使权力'
    const out = highlightIssues(html, [
      { index: 0, original: '权力', severity: 'warning', type: 'typo', suggestion: '权利' },
      { index: 1, original: '权力机关', severity: 'info', type: 'style', suggestion: '' },
    ], mark)
    // 「权力机关」整体高亮（索引 1），其后独立的「权力」单独高亮（索引 0）
    expect(out).toBe('<mark class="hl-info">权力机关</mark>行使<mark class="hl-warning">权力</mark>')
  })

  it('同一原文重复上报不产生嵌套 mark', () => {
    const html = '帐号错了'
    const out = highlightIssues(html, [
      { index: 0, original: '帐号', severity: 'error', type: 'typo', suggestion: '账号' },
      { index: 1, original: '帐号', severity: 'error', type: 'typo', suggestion: '账号' },
    ], mark)
    expect(out).toBe('<mark class="hl-error">帐号</mark>错了')
  })

  it('原文含 HTML 特殊字符时正确转义', () => {
    // 真实链路传入的 html 已整体 escapeHtml（& → &amp;），<b> 为既有标签
    const html = 'a<b>c &amp; d'
    const out = highlightIssues(html, [
      { index: 0, original: 'c & d', severity: 'warning', type: 'typo', suggestion: 'x' },
    ], mark)
    expect(out).toBe('a<b><mark class="hl-warning">c &amp; d</mark>')
  })
})

describe('replaceTextInHtml', () => {
  it('替换文本节点内的全部出现，跳过标签', () => {
    const html = '<p>帐号一</p><span title="帐号">帐号二</span>'
    const out = replaceTextInHtml(html, '帐号', '账号')
    expect(out).toBe('<p>账号一</p><span title="帐号">账号二</span>')
  })
})
