import { describe, expect, it } from 'vitest'
import { applyChunkRetry } from '../proofreadCoverage'
import type { ProofreadCoverage, TextProofreadResponse } from '@/api/proofread'

const target = { chunk_index: 2, start: 10, end: 15, text: '甲乙丙丁戊', error_code: 'MODEL_ERROR' }
const other = { chunk_index: 3, start: 15, end: 18, text: '己庚辛', error_code: 'MODEL_ERROR' }
const coverage: ProofreadCoverage = { status: 'partial', total_chunks: 4, completed_chunks: 2, failed_chunks: [target, other] }
const response: TextProofreadResponse = {
  issues: [{ original: '乙', suggestion: '已', severity: 'error', type: 'typo', start: 1, end: 2 }],
  total_issues: 1, chunks_count: 1, usage: {}, domain: 'general', check_types: [],
  coverage: { status: 'complete', total_chunks: 1, completed_chunks: 1, failed_chunks: [] },
}

describe('失败片段补查合并', () => {
  it('只移除本次失败范围并将问题位置映射回原文', () => {
    const next = applyChunkRetry(coverage, target, response)
    expect(next.issues[0]).toMatchObject({ start: 11, end: 12, chunk_index: 2 })
    expect(next.coverage).toEqual({ status: 'partial', total_chunks: 4, completed_chunks: 3, failed_chunks: [other] })
    expect(coverage.failed_chunks).toHaveLength(2)
  })
  it('最后一个片段完成后才标为complete', () => {
    const first = applyChunkRetry(coverage, target, response)
    expect(applyChunkRetry(first.coverage, other, { ...response, issues: [] }).coverage.status).toBe('complete')
  })
  it('部分补查失败继续保留准确的未审区间', () => {
    const next = applyChunkRetry(coverage, target, {
      ...response,
      coverage: { status: 'partial', total_chunks: 2, completed_chunks: 1, failed_chunks: [{ chunk_index: 1, start: 3, end: 5, text: '丁戊', error_code: 'MODEL_ERROR' }] },
    })
    expect(next.coverage.status).toBe('partial')
    expect(next.coverage.failed_chunks[0]).toMatchObject({ start: 13, end: 15, text: '丁戊', chunk_index: 2 })
  })
  it('多个失败子块分配唯一编号且覆盖统计仍然一致', () => {
    const next = applyChunkRetry(coverage, target, {
      ...response,
      coverage: { status: 'partial', total_chunks: 2, completed_chunks: 0, failed_chunks: [
        { chunk_index: 0, start: 0, end: 3, text: '甲乙丙', error_code: 'MODEL_ERROR' },
        { chunk_index: 1, start: 3, end: 5, text: '丁戊', error_code: 'MODEL_ERROR' },
      ] },
    })
    const result = next.coverage
    expect(new Set(result.failed_chunks.map(chunk => chunk.chunk_index)).size).toBe(3)
    expect(result.completed_chunks + result.failed_chunks.length).toBe(result.total_chunks)
    expect(result.failed_chunks.every(chunk => chunk.chunk_index < result.total_chunks)).toBe(true)
  })
  it('补查响应未带覆盖范围时不可声称已完成', () => {
    expect(() => applyChunkRetry(coverage, target, { ...response, coverage: undefined })).toThrow('缺少覆盖范围')
  })
  it('未定位的问题不能被错误映射到全文首处', () => {
    const next = applyChunkRetry(coverage, target, { ...response, issues: [{ ...response.issues[0]!, start: null, end: null }] })
    expect(next.issues[0]).toMatchObject({ start: -1, end: -1 })
  })
})
