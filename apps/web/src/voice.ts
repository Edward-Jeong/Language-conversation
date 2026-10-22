import type { MediaState } from './types'

export const VOICE_LIMIT_MS = 45_000
export const SILENCE_MS = 1_500

export function selectRecorderMime(accepted: string[], supported = (type: string) => MediaRecorder.isTypeSupported(type)) {
  const preference = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'video/mp4']
  return preference.find((type) => accepted.some((allowed) => type === allowed || type.split(';')[0] === allowed) && supported(type)) ?? ''
}

type Callbacks = {
  state: (state: MediaState, detail?: string) => void
  audio: (blob: Blob, timing: { speechEndedAt: number; recorderStoppedAt: number }) => void
  pauseRequired: (reason: string) => void
  voice: (description: string) => void
  timing: (name: 'speechQueuedAt' | 'speechStartedAt', at: number) => void
}

export class VoiceLoop {
  private stream?: MediaStream
  private recorder?: MediaRecorder
  private context?: AudioContext
  private analyser?: AnalyserNode
  private frame = 0
  private chunks: Blob[] = []
  private startedAt = 0
  private lastVoiceAt = 0
  private heardVoice = false
  private epoch = 0
  private boundLifecycle = false

  constructor(private callbacks: Callbacks) {}

  bindLifecycle() {
    if (this.boundLifecycle) return
    this.boundLifecycle = true
    const suspend = (reason: string) => {
      this.cancel(reason)
      this.callbacks.pauseRequired(reason)
    }
    document.addEventListener('visibilitychange', () => { if (document.hidden) suspend('앱이 백그라운드로 이동했어요.') })
    window.addEventListener('offline', () => suspend('네트워크 연결이 끊겼어요.'))
    window.addEventListener('pagehide', () => suspend('페이지가 닫혀 음성을 중지했어요.'))
    navigator.mediaDevices?.addEventListener?.('devicechange', () => suspend('오디오 장치가 바뀌었어요.'))
  }

  async listen(acceptedMime: string[]) {
    const current = ++this.epoch
    await this.releaseInput()
    speechSynthesis.cancel()
    this.callbacks.state('preparing')
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('이 브라우저는 음성 녹음을 지원하지 않아요. 텍스트 입력을 사용해 주세요.')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false })
      if (current !== this.epoch) { stream.getTracks().forEach((track) => track.stop()); return }
      this.stream = stream
      this.context = new AudioContext()
      await this.context.resume()
      const source = this.context.createMediaStreamSource(stream)
      this.analyser = this.context.createAnalyser()
      this.analyser.fftSize = 1024
      source.connect(this.analyser)
      const mimeType = selectRecorderMime(acceptedMime)
      this.recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
      this.chunks = []
      this.heardVoice = false
      this.startedAt = performance.now()
      this.lastVoiceAt = this.startedAt
      this.recorder.ondataavailable = (event) => { if (event.data.size) this.chunks.push(event.data) }
      this.recorder.onstop = () => {
        const type = this.recorder?.mimeType || mimeType || 'audio/webm'
        const blob = new Blob(this.chunks, { type })
        void this.releaseInput()
        if (current === this.epoch && this.heardVoice && blob.size > 0) this.callbacks.audio(blob, { speechEndedAt: this.lastVoiceAt, recorderStoppedAt: performance.now() })
        else if (current === this.epoch) this.callbacks.state('idle', '목소리가 들리지 않았어요. 다시 말하거나 텍스트로 입력해 주세요.')
      }
      this.recorder.start(250)
      this.callbacks.state('listening')
      this.monitor(current)
    } catch (error) {
      await this.releaseInput()
      this.callbacks.state('error', error instanceof DOMException && error.name === 'NotAllowedError' ? '마이크 권한이 거부됐어요. 브라우저 설정에서 허용해 주세요.' : error instanceof Error ? error.message : '마이크를 시작하지 못했어요.')
      throw error
    }
  }

  private monitor(current: number) {
    if (current !== this.epoch || !this.analyser || !this.recorder) return
    const values = new Uint8Array(this.analyser.fftSize)
    this.analyser.getByteTimeDomainData(values)
    let sum = 0
    for (const value of values) { const sample = (value - 128) / 128; sum += sample * sample }
    const rms = Math.sqrt(sum / values.length)
    const now = performance.now()
    if (rms > 0.018) { this.heardVoice = true; this.lastVoiceAt = now }
    const silentAfterSpeech = this.heardVoice && now - this.lastVoiceAt >= SILENCE_MS
    const capped = now - this.startedAt >= VOICE_LIMIT_MS
    if (silentAfterSpeech || capped) { this.finish(); return }
    this.frame = requestAnimationFrame(() => this.monitor(current))
  }

  finish() {
    if (this.recorder?.state === 'recording') {
      cancelAnimationFrame(this.frame)
      this.callbacks.state('transcribing')
      this.recorder.stop()
    }
  }

  speak(text: string, autoListen: () => void) {
    const current = ++this.epoch
    void this.releaseInput()
    speechSynthesis.cancel()
    if (!('speechSynthesis' in window) || !text.trim()) { this.callbacks.state('error', '기기 영어 음성을 재생할 수 없어요. 질문을 읽고 수동으로 시작해 주세요.'); return }
    this.callbacks.timing('speechQueuedAt', performance.now())
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = 'en-US'
    const voices = speechSynthesis.getVoices()
    const voice = voices.find((item) => item.lang.toLowerCase().startsWith('en') && item.localService) ?? voices.find((item) => item.lang.toLowerCase().startsWith('en'))
    if (voice) utterance.voice = voice
    this.callbacks.voice(voice ? `${voice.name} · ${voice.localService ? '기기 음성' : '브라우저 제공 음성'}` : '브라우저 기본 영어 음성')
    utterance.onstart = () => { if (current === this.epoch) { this.callbacks.timing('speechStartedAt', performance.now()); this.callbacks.state('speaking') } }
    utterance.onerror = () => { if (current === this.epoch) this.callbacks.state('error', '음성 재생에 실패했어요. 화면의 질문으로 계속할 수 있어요.') }
    utterance.onend = () => { if (current === this.epoch && !document.hidden && navigator.onLine) autoListen() }
    speechSynthesis.speak(utterance)
  }

  cancel(detail = '음성을 중지했어요.') {
    this.epoch += 1
    cancelAnimationFrame(this.frame)
    speechSynthesis.cancel()
    void this.releaseInput()
    this.callbacks.state('paused', detail)
  }

  private async releaseInput() {
    if (this.recorder?.state === 'recording') { this.recorder.onstop = null; this.recorder.stop() }
    this.recorder = undefined
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = undefined
    this.analyser?.disconnect()
    this.analyser = undefined
    if (this.context && this.context.state !== 'closed') await this.context.close().catch(() => undefined)
    this.context = undefined
  }
}
