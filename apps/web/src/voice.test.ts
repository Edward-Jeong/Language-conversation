import { describe, expect, it } from 'vitest'
import { selectRecorderMime, SILENCE_MS, VOICE_LIMIT_MS } from './voice'

describe('voice recording boundaries', () => {
  it('prefers an actually supported server-accepted mobile format', () => {
    expect(selectRecorderMime(['audio/webm', 'audio/mp4'], (type) => type === 'audio/mp4')).toBe('audio/mp4')
    expect(selectRecorderMime(['audio/webm'], (type) => type === 'audio/webm;codecs=opus')).toBe('audio/webm;codecs=opus')
  })
  it('returns browser default when no declared format overlaps', () => {
    expect(selectRecorderMime(['audio/ogg'], () => true)).toBe('')
  })
  it('keeps the specified silence and hard recording caps', () => {
    expect(SILENCE_MS).toBe(1500)
    expect(VOICE_LIMIT_MS).toBe(45000)
  })
})
