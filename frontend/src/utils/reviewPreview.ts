import { escapeHtml, typeLabel } from './proofread'

interface PreviewIssue {
  start?: number | null
  end?: number | null
  original: string
  suggestion: string
  type: string
  severity: string
  _accepted?: boolean
  _ignored?: boolean
}
interface PreviewPatch { start: number; end: number; replacement: string }

export function buildReviewPreview(text: string, issues: PreviewIssue[], patches: PreviewPatch[]): string {
  const sortedPatches = [...patches].sort((a, b) => a.start - b.start)
  const deltas = [0]
  for (const p of sortedPatches) deltas.push(deltas[deltas.length - 1]! + Array.from(p.replacement).length - (p.end - p.start))
  const before = (position: number) => {
    let low = 0
    let high = sortedPatches.length
    while (low < high) {
      const mid = (low + high) >>> 1
      if (sortedPatches[mid]!.end <= position) low = mid + 1
      else high = mid
    }
    return low
  }
  const entries = issues.flatMap((issue, index) => {
    const { start, end } = issue
    if (issue._accepted || issue._ignored || start == null || end == null || start < 0 || end <= start) return []
    const left = before(start)
    if (sortedPatches[left] && sortedPatches[left]!.start < end) return []
    return [{ issue, index, start: start + deltas[left]!, end: end + deltas[before(end)]! }]
  }).sort((a, b) => a.start - b.start || b.end - a.end)
  const chars = Array.from(text)
  const html: string[] = []
  let cursor = 0
  for (const entry of entries) {
    if (entry.start < cursor || entry.end > chars.length) continue
    html.push(escapeHtml(chars.slice(cursor, entry.start).join('')))
    const severity = ['error', 'warning', 'info'].includes(entry.issue.severity) ? entry.issue.severity : 'info'
    html.push(`<mark data-issue-idx="${entry.index}" class="hl-${severity}" title="${escapeHtml(typeLabel(entry.issue.type))}">${escapeHtml(chars.slice(entry.start, entry.end).join(''))}</mark>`)
    cursor = entry.end
  }
  html.push(escapeHtml(chars.slice(cursor).join('')))
  return html.join('')
}
