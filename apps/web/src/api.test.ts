import { describe, expect, it } from 'vitest'
import { buildAudioForm, buildReviewAudioForm } from './api'

const question = { turn_id: 'q1', text_en: 'How was your day?', intent_id: 'daily-story', task_variant: 'initial' as const }

describe('audio upload metadata', () => {
  it('includes question metadata on an initial answer', () => {
    const form = buildAudioForm(new Blob(['audio'], { type: 'audio/webm' }), question, false)
    expect(form.get('intent_id')).toBe('daily-story')
    expect(form.get('task_variant')).toBe('initial')
  })

  it('omits intent and task metadata during a server-owned retry', () => {
    const form = buildAudioForm(new Blob(['audio'], { type: 'audio/mp4' }), question, true)
    expect(form.has('intent_id')).toBe(false)
    expect(form.has('task_variant')).toBe(false)
    expect((form.get('file') as File).name).toBe('answer.m4a')
  })
  it('keeps review audio free of client-side outcome claims', () => {
    const form = buildReviewAudioForm(new Blob(['audio'], { type: 'audio/webm' }))
    expect([...form.keys()].sort()).toEqual(['client_attempt_id', 'file'])
  })
})
