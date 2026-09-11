import { describe, expect, it } from 'vitest'
import { formatSize, formatTime } from '../format'

describe('formatTime', () => {
  it('空值返回 -', () => {
    expect(formatTime(null)).toBe('-')
    expect(formatTime(undefined)).toBe('-')
    expect(formatTime('')).toBe('-')
  })

  it('ISO 字符串格式化为 YYYY/MM/DD HH:mm:ss', () => {
    const result = formatTime('2026-09-11T03:24:06+00:00')
    expect(result).toMatch(/2026/)
    expect(result).not.toContain('T')
  })
})

describe('formatSize', () => {
  it('按量级换算单位', () => {
    expect(formatSize(0)).toBe('0 B')
    expect(formatSize(1024)).toBe('1.0 KB')
    expect(formatSize(1536)).toBe('1.5 KB')
    expect(formatSize(1024 * 1024)).toBe('1.0 MB')
  })
})
