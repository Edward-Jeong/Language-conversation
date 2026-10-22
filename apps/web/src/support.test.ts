import { describe, expect, it } from 'vitest'
import { canCaptureVoice, lifecycleRecoveryNeedsPause, resumeSpeechText, shouldHeartbeat, shouldPauseRestoredSession, shouldSuspendForLifecycle, supportCopy } from './App'
import type { Evaluation, SessionState } from './types'

describe('sentence support descriptions', () => {
  it('keeps each learner-controlled support level distinct', () => {
    expect(supportCopy('A')).toContain('문장 뼈대')
    expect(supportCopy('B')).toContain('의미 방향')
    expect(supportCopy('C')).toContain('질문만')
  })
})

describe('safe session recovery', () => {
  const session = { id:'s', scenario_id:'cafe', status:'active', learning_state:'retry', media_state:'idle', question:{ turn_id:'q', text_en:'Original question', intent_id:'order', task_variant:'initial' }, pending_next_question:true, retry_count:1, max_retries:2, response_revision:1, updated_at:'' } satisfies SessionState
  const evaluation = { correction:{ needed:true, original_span:'I want', suggested_en:'I would like a latte, please.', reason_ko:'더 자연스러운 요청' } } as Evaluation

  it('server-pauses an active restored session before showing resume', () => {
    expect(shouldPauseRestoredSession(session)).toBe(true)
    expect(shouldPauseRestoredSession({ ...session, status:'paused' })).toBe(false)
  })
  it('replays the exposed correction instead of the original question on retry resume', () => {
    expect(resumeSpeechText(session, evaluation)).toBe('I would like a latte, please.')
    expect(resumeSpeechText({ ...session, learning_state:'question' }, evaluation)).toBe('Original question')
  })
  it('suspends active processing even when no audio resource is open', () => {
    expect(shouldSuspendForLifecycle(session, false)).toBe(true)
    expect(shouldSuspendForLifecycle({ ...session, status:'paused' }, false)).toBe(false)
    expect(shouldSuspendForLifecycle(null, true)).toBe(true)
  })
  it('never opens or accepts the conversation microphone after the session ends', () => {
    expect(canCaptureVoice(session, false)).toBe(true)
    expect(canCaptureVoice({ ...session, status:'ended' }, false)).toBe(false)
    expect(canCaptureVoice(null, true)).toBe(true)
  })
  it('reconciles a lifecycle pause that the server did not acknowledge before resuming', () => {
    expect(lifecycleRecoveryNeedsPause(session, true)).toBe(true)
    expect(lifecycleRecoveryNeedsPause({ ...session, status:'paused' }, true)).toBe(false)
    expect(lifecycleRecoveryNeedsPause(session, false)).toBe(false)
  })
  it('heartbeats only an active visible online conversation', () => {
    expect(shouldHeartbeat('conversation', session, true, true, false)).toBe(true)
    expect(shouldHeartbeat('home', session, true, true, false)).toBe(false)
    expect(shouldHeartbeat('conversation', { ...session, status:'paused' }, true, true, false)).toBe(false)
    expect(shouldHeartbeat('conversation', session, false, true, false)).toBe(false)
    expect(shouldHeartbeat('conversation', session, true, false, false)).toBe(false)
    expect(shouldHeartbeat('conversation', session, true, true, true)).toBe(false)
    expect(shouldHeartbeat('conversation', session, true, true, false)).toBe(true)
  })
})
