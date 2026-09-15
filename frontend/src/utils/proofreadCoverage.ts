import type { FailedProofreadChunk, ProofreadCoverage, ProofreadIssue, TextProofreadResponse } from '@/api/proofread'

export function applyChunkRetry(
  coverage: ProofreadCoverage,
  target: FailedProofreadChunk,
  result: TextProofreadResponse,
): { coverage: ProofreadCoverage; issues: ProofreadIssue[] } {
  const failed = coverage.failed_chunks.filter(c => c.start !== target.start || c.end !== target.end)
  if (!result.coverage) throw new Error('补查结果缺少覆盖范围，请重试')
  const remaining = result.coverage.failed_chunks.map((c, index) => ({
    ...c,
    chunk_index: index === 0 ? target.chunk_index : coverage.total_chunks + index - 1,
    start: target.start + c.start,
    end: target.start + c.end,
  }))
  failed.push(...remaining)
  failed.sort((a, b) => a.start - b.start)
  const total = coverage.total_chunks + Math.max(0, remaining.length - 1)
  const issues = result.issues.map(issue => ({
    ...issue,
    chunk_index: target.chunk_index,
    start: typeof issue.start === 'number' && issue.start >= 0 ? target.start + issue.start : -1,
    end: typeof issue.end === 'number' && issue.end >= 0 ? target.start + issue.end : -1,
  }))
  return {
    issues,
    coverage: {
      status: failed.length ? 'partial' : 'complete',
      total_chunks: total,
      completed_chunks: total - failed.length,
      failed_chunks: failed,
    },
  }
}
