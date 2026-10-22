import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, pairingErrorMessage } from './api'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('device pairing failure recovery', () => {
  it('explains expired or reused codes and rate limits in Korean', () => {
    expect(pairingErrorMessage(new ApiError(401, 'invalid_pairing_code', 'internal English'))).toContain('새 연결 코드')
    expect(pairingErrorMessage(new ApiError(429, 'pairing_rate_limited', 'internal English'))).toContain('10분')
    expect(pairingErrorMessage(new ApiError(422, 'validation_error', 'internal English'))).toContain('8자리')
  })
  it('bounds a stuck pairing fetch and offers a visible retry message', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('document', { cookie: '' })
    vi.stubGlobal('fetch', vi.fn((_path, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const pending = api.pair('ABCDEFGH')
    const assertion = expect(pending).rejects.toMatchObject({code:'pairing_timeout',status:504})
    await vi.advanceTimersByTimeAsync(15_000)
    await assertion
    expect(pairingErrorMessage(new ApiError(504,'pairing_timeout',''))).toContain('응답이 늦어')
  })
  it('keeps a server rate-limit error available to the form', async () => {
    vi.stubGlobal('document', { cookie: '' })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({detail:{code:'pairing_rate_limited',message:'Too many attempts'}}), {status:429})))
    await expect(api.pair('ABCDEFGH')).rejects.toMatchObject({status:429,code:'pairing_rate_limited'})
  })
})
