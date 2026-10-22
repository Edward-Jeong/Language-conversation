import { useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, ChevronRight, CircleUserRound, Clock3, Coffee, Download, Headphones, History, Home, Lightbulb, LoaderCircle, LogOut, MessageCircle, Mic, Pause, Play, RotateCcw, Settings, ShieldCheck, Square, Trash2, Upload, Volume2, WifiOff } from 'lucide-react'
import { ApiError, api, setCsrfToken } from './api'
import type { Capabilities, Evaluation, HistorySession, MediaState, ReviewItem, ReviewResult, Scenario, SessionState, SessionSummary, SupportProgress, Turn } from './types'
import { VoiceLoop } from './voice'

type Page = 'home' | 'conversation' | 'review' | 'history' | 'settings'
const NAV: Array<{ id: Page; label: string; icon: typeof Home }> = [
  { id: 'home', label: '홈', icon: Home }, { id: 'conversation', label: '대화', icon: MessageCircle },
  { id: 'review', label: '복습', icon: BookOpen }, { id: 'history', label: '기록', icon: History }, { id: 'settings', label: '설정', icon: Settings },
]
const MEDIA_LABEL: Record<MediaState, string> = {
  idle: '준비됨', preparing: '마이크 준비 중', listening: '듣고 있어요', transcribing: '말을 글로 옮기는 중', processing: '답변을 살펴보는 중', speaking: '튜터가 말하는 중', paused: '일시정지됨', reconnecting: '다시 연결하는 중', ended: '대화 종료', error: '확인이 필요해요',
}

function messageOf(error: unknown) {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : '요청을 완료하지 못했어요.'
}
function isAbort(error: unknown) { return error instanceof DOMException && error.name === 'AbortError' }

export function App() {
  const [page, setPage] = useState<Page>('home')
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [reviews, setReviews] = useState<ReviewItem[]>([])
  const [history, setHistory] = useState<HistorySession[]>([])
  const [reviewResults, setReviewResults] = useState<Record<string, ReviewResult>>({})
  const [session, setSession] = useState<SessionState | null>(null)
  const [summary, setSummary] = useState<SessionSummary | null>(null)
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [lastTurn, setLastTurn] = useState<Turn | null>(null)
  const [transcript, setTranscript] = useState('')
  const [draft, setDraft] = useState('')
  const [hint, setHint] = useState<{ level: string; content: string } | null>(null)
  const [supportProgress, setSupportProgress] = useState<SupportProgress | null>(null)
  const [alternativeVisible, setAlternativeVisible] = useState(false)
  const [media, setMedia] = useState<MediaState>('idle')
  const [mediaDetail, setMediaDetail] = useState('')
  const [voiceName, setVoiceName] = useState('기기 영어 음성 확인 전')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pausedByLifecycle, setPausedByLifecycle] = useState(false)
  const [showTranslation, setShowTranslation] = useState(() => localStorage.getItem('lc_translation') === 'true')
  const [sessionMinutes, setSessionMinutes] = useState(() => Number(localStorage.getItem('lc_minutes') || 10))
  const [pairCode, setPairCode] = useState('')
  const [pairBusy, setPairBusy] = useState(false)
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState === 'visible')
  const [online, setOnline] = useState(() => navigator.onLine)
  const [latency, setLatency] = useState<{ vadMs?: number; serverRoundTripMs?: number; ttsQueueMs?: number; totalMs?: number }>({})
  const sessionRef = useRef<SessionState | null>(null)
  const uploadRef = useRef<(blob: Blob, timing: { speechEndedAt: number; recorderStoppedAt: number }) => void>(() => undefined)
  const timingRef = useRef<{ speechEndedAt?: number; speechQueuedAt?: number }>({})
  const operationEpoch = useRef(0)
  const requestController = useRef<AbortController | null>(null)
  const reviewVoiceTarget = useRef<ReviewItem | null>(null)
  const lifecyclePause = useRef<Promise<SessionState | null> | null>(null)
  sessionRef.current = session

  function beginOperation() {
    requestController.current?.abort()
    requestController.current = new AbortController()
    operationEpoch.current += 1
    return { epoch: operationEpoch.current, signal: requestController.current.signal }
  }
  function cancelPending() { requestController.current?.abort(); requestController.current = null; operationEpoch.current += 1; setBusy(false) }
  function current(epoch: number) { return operationEpoch.current === epoch }

  const voice = useMemo(() => new VoiceLoop({
    state: (next, detail) => { setMedia(next); setMediaDetail(detail ?? '') },
    audio: (blob, timing) => uploadRef.current(blob, timing),
    pauseRequired: (reason) => {
      const active = sessionRef.current
      if (!shouldSuspendForLifecycle(active, Boolean(reviewVoiceTarget.current))) return
      cancelPending()
      setPausedByLifecycle(true); setError(reason + ' 다시 시작하려면 아래 버튼을 눌러 주세요.')
      const lifecycleEpoch = operationEpoch.current
      if (active?.status === 'active' && !lifecyclePause.current) {
        lifecyclePause.current = api.pause(active.id).then((paused) => { if (current(lifecycleEpoch)) setSession(paused); return paused }).catch(() => null)
      }
    },
    voice: setVoiceName,
    timing: (name, at) => {
      if (name === 'speechQueuedAt') timingRef.current.speechQueuedAt = at
      else {
        const queued = timingRef.current.speechQueuedAt
        const ended = timingRef.current.speechEndedAt
        setLatency((current) => ({ ...current, ttsQueueMs: queued ? at - queued : undefined, totalMs: ended ? at - ended : undefined }))
      }
    },
  }), [])

  useEffect(() => { voice.bindLifecycle(); void bootstrap() }, [voice])
  useEffect(() => {
    const visibility = () => setPageVisible(document.visibilityState === 'visible')
    const connected = () => setOnline(true)
    const disconnected = () => setOnline(false)
    document.addEventListener('visibilitychange', visibility); window.addEventListener('online', connected); window.addEventListener('offline', disconnected)
    return () => { document.removeEventListener('visibilitychange', visibility); window.removeEventListener('online', connected); window.removeEventListener('offline', disconnected) }
  }, [])
  useEffect(() => { localStorage.setItem('lc_translation', String(showTranslation)) }, [showTranslation])
  useEffect(() => { localStorage.setItem('lc_minutes', String(sessionMinutes)) }, [sessionMinutes])
  useEffect(() => {
    if (!shouldHeartbeat(page, session, pageVisible, online, pausedByLifecycle)) return
    const sessionId = session!.id
    const controller = new AbortController()
    let stopped = false
    let disposed = false
    let timer: number | undefined
    const pulse = async () => {
      try { await api.heartbeat(sessionId, controller.signal) }
      catch (cause) {
        if (disposed || isAbort(cause)) return
        stopped = true
        if (timer != null) window.clearInterval(timer)
        cancelPending(); voice.cancel('PC 연결 확인이 멈춰 대화를 안전하게 일시정지했어요.'); setError('PC 연결 확인에 실패했어요. 연결을 확인한 뒤 안전하게 다시 시작해 주세요.')
        const guardEpoch = operationEpoch.current
        if (cause instanceof ApiError && cause.status === 409) {
          try { const authoritative = await api.session(sessionId); if (!disposed && current(guardEpoch)) setSession(authoritative) } catch { /* explicit resume remains available */ }
          if (!disposed && current(guardEpoch)) setPausedByLifecycle(true)
        } else if (!lifecyclePause.current) {
          setPausedByLifecycle(true)
          lifecyclePause.current = api.pause(sessionId).then((paused) => { if (current(guardEpoch)) setSession(paused); return paused }).catch(() => null)
        } else {
          setPausedByLifecycle(true)
        }
      }
    }
    void pulse()
    timer = window.setInterval(() => void pulse(), 15_000)
    return () => { disposed = true; stopped = true; if (timer != null) window.clearInterval(timer); controller.abort() }
  }, [page, session?.id, session?.status, pageVisible, online, pausedByLifecycle, voice])

  async function bootstrap() {
    setError('')
    try {
      const capability = await api.capabilities()
      setCaps(capability)
      if (capability.authenticated) await loadPrivate()
    } catch (cause) { setError(messageOf(cause)) }
  }

  async function loadPrivate() {
    const [scenarioResult, reviewResult, historyResult] = await Promise.allSettled([api.scenarios(), api.reviews(), api.history()])
    if (scenarioResult.status === 'fulfilled') setScenarios(scenarioResult.value.items)
    if (reviewResult.status === 'fulfilled') setReviews(reviewResult.value.items)
    if (historyResult.status === 'fulfilled') setHistory(historyResult.value.sessions)
  }

  async function pair(event: React.FormEvent) {
    event.preventDefault(); setPairBusy(true); setError('')
    try {
      const result = await api.pair(pairCode.trim())
      setCsrfToken(result.csrf_token)
      setCaps((current) => current ? { ...current, authenticated: true } : current)
      setPairCode('')
      await loadPrivate()
    } catch (cause) { setError(messageOf(cause)) } finally { setPairBusy(false) }
  }

  function listen() {
    if (!canCaptureVoice(sessionRef.current, Boolean(reviewVoiceTarget.current))) return
    setError(''); setPausedByLifecycle(false)
    void voice.listen(caps?.speech_input.accepted_mime_types ?? ['audio/mp4', 'audio/webm']).catch(() => undefined)
  }

  function speakQuestion(next: SessionState) {
    if (!next.question) { setMedia('idle'); return }
    voice.speak(next.question.text_en, listen)
  }

  function navigate(next: Page) {
    if (next !== 'review' && reviewVoiceTarget.current) { reviewVoiceTarget.current = null; cancelPending(); voice.cancel('복습 음성을 중지했어요.') }
    if (next !== 'conversation' && sessionRef.current?.status === 'active') {
      cancelPending(); voice.cancel('대화 화면을 떠나 음성을 멈췄어요.'); setPausedByLifecycle(true)
      const leavingSessionId = sessionRef.current.id
      const navigationEpoch = operationEpoch.current
      void api.pause(leavingSessionId).then((paused) => {
        if (current(navigationEpoch) && sessionRef.current?.id === leavingSessionId) setSession(paused)
      }).catch(() => undefined)
    }
    setPage(next)
  }

  function acceptResult(result: { session: SessionState; evaluation: Evaluation; turn: Turn; support_progress?: SupportProgress | null }) {
    setSession(result.session); setEvaluation(result.evaluation); setLastTurn(result.turn)
    setTranscript(result.turn.final_text ?? result.turn.text ?? '')
    setSupportProgress(result.support_progress ?? null)
    setAlternativeVisible(false)
    setHint(null); setDraft(''); setMedia('idle')
    if (result.evaluation.correction.needed && result.evaluation.correction.suggested_en) {
      voice.speak(result.evaluation.correction.suggested_en, listen)
    } else if (result.session.question && !result.session.pending_next_question) speakQuestion(result.session)
  }

  uploadRef.current = async (blob: Blob, timing: { speechEndedAt: number; recorderStoppedAt: number }) => {
    const reviewTarget = reviewVoiceTarget.current
    if (reviewTarget) {
      const operation = beginOperation(); setMedia('transcribing'); setBusy(true); setError('')
      try {
        const result = await api.reviewAudio(reviewTarget.id, blob, operation.signal)
        if (current(operation.epoch)) {
          setReviewResults((values) => ({ ...values, [reviewTarget.id]: result }))
          if (result.evaluation.correction.needed && result.evaluation.correction.suggested_en) voice.speak(result.evaluation.correction.suggested_en, listen)
          else { reviewVoiceTarget.current = null; setMedia('idle') }
        }
      }
      catch (cause) { if (!isAbort(cause) && current(operation.epoch)) { setMedia('error'); setError(messageOf(cause)) } }
      finally { if (current(operation.epoch)) setBusy(false) }
      return
    }
    const active = sessionRef.current
    if (!active || active.status !== 'active') return
    setMedia('transcribing'); setBusy(true); setError('')
    timingRef.current = { speechEndedAt: timing.speechEndedAt }
    const uploadStartedAt = performance.now()
    setLatency({ vadMs: timing.recorderStoppedAt - timing.speechEndedAt })
    const operation = beginOperation()
    try {
      const result = await api.submitAudio(active.id, blob, active.question, operation.signal, active.learning_state === 'retry')
      if (!current(operation.epoch)) return
      setLatency((current) => ({ ...current, serverRoundTripMs: performance.now() - uploadStartedAt }))
      acceptResult(result)
    }
    catch (cause) { if (!isAbort(cause) && current(operation.epoch)) { setMedia('error'); setError(messageOf(cause)) } }
    finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function startScenario(scenarioId: string) {
    const operation = beginOperation(); lifecyclePause.current = null; setBusy(true); setError(''); voice.cancel('새 대화를 준비해요.')
    try {
      const created = await api.createSession(scenarioId, operation.signal)
      if (!current(operation.epoch)) return
      setSession(created); sessionRef.current = created; setSummary(null); setEvaluation(null); setLastTurn(null); setTranscript(''); setHint(null); setSupportProgress(null)
      localStorage.setItem('lc_last_session', created.id)
      setPage('conversation'); setPausedByLifecycle(false); speakQuestion(created)
    } catch (cause) { if (!isAbort(cause) && current(operation.epoch)) setError(messageOf(cause)) } finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function resumeSaved() {
    const saved = localStorage.getItem('lc_last_session')
    if (!saved) return
    const operation = beginOperation(); setBusy(true); setError('')
    try {
      const restored = await api.session(saved, operation.signal)
      if (!current(operation.epoch)) return
      const safeRestored = shouldPauseRestoredSession(restored) ? await api.pause(restored.id, operation.signal) : restored
      if (!current(operation.epoch)) return
      setSession(safeRestored); sessionRef.current = safeRestored; setSummary(safeRestored.summary ?? null); setPage('conversation'); setPausedByLifecycle(safeRestored.status !== 'ended'); setMedia(safeRestored.status === 'ended' ? 'ended' : 'paused')
      setLastTurn(safeRestored.current_turn ?? null); setEvaluation(safeRestored.current_evaluation ?? null); setTranscript(safeRestored.current_turn?.final_text ?? safeRestored.current_turn?.text ?? '')
      if (safeRestored.learning_state === 'retry' && (!safeRestored.current_turn || !safeRestored.current_evaluation)) setError('재시도 문맥을 복구하지 못했어요. PC 앱을 업데이트한 뒤 다시 불러와 주세요.')
    } catch (cause) { if (!isAbort(cause) && current(operation.epoch)) { localStorage.removeItem('lc_last_session'); setError(messageOf(cause)) } } finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function submitText() {
    if (!session || session.status !== 'active' || !draft.trim()) return
    const operation = beginOperation(); setBusy(true); setMedia('processing'); setError(''); voice.cancel('텍스트 답변을 처리해요.')
    try {
      const result = session.learning_state === 'retry' && lastTurn ? await api.retry(lastTurn.id, draft.trim(), operation.signal) : await api.submitText(session.id, draft.trim(), session.question, operation.signal)
      if (!current(operation.epoch)) return
      acceptResult(result)
    } catch (cause) { if (!isAbort(cause) && current(operation.epoch)) { setMedia('error'); setError(messageOf(cause)) } } finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function saveTranscript() {
    if (session?.status !== 'active' || !lastTurn || !transcript.trim()) return
    const operation = beginOperation(); setBusy(true); setError(''); voice.cancel('수정한 전사를 다시 평가해요.')
    try { const result = await api.editTurn(lastTurn.id, transcript.trim(), operation.signal); if (current(operation.epoch)) acceptResult(result) }
    catch (cause) { if (!isAbort(cause) && current(operation.epoch)) setError(messageOf(cause)) } finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function markTranscriptSuspect() {
    if (session?.status !== 'active' || !lastTurn) return
    const operation = beginOperation(); setBusy(true); setError(''); voice.cancel('전사 확인을 위해 평가를 멈췄어요.')
    try { const result = await api.markTranscriptSuspect(lastTurn.id, operation.signal); if (current(operation.epoch)) acceptResult(result) }
    catch (cause) { if (!isAbort(cause) && current(operation.epoch)) setError(messageOf(cause)) } finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function requestHint() {
    if (session?.status !== 'active' || !session.question) return
    const operation = beginOperation(); setBusy(true); setError('')
    try { const result = await api.hint(session.question.turn_id, operation.signal); if (current(operation.epoch)) { setHint(result.hint); setSession(result.session) } }
    catch (cause) { if (!isAbort(cause) && current(operation.epoch)) setError(messageOf(cause)) } finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function continueAfterCorrection() {
    if (session?.status !== 'active' || !lastTurn) return
    const operation = beginOperation(); setBusy(true); setError('')
    try {
      const raw = await api.continueTurn(lastTurn.id, operation.signal)
      if (!current(operation.epoch)) return
      const next = 'session' in raw ? raw.session : raw
      setSession(next); setEvaluation(null); setLastTurn(null); setTranscript(''); setDraft(''); speakQuestion(next)
    } catch (cause) { if (!isAbort(cause) && current(operation.epoch)) setError(messageOf(cause)) } finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function acceptSupportProposal() {
    if (session?.status !== 'active' || !supportProgress?.proposed_level) return
    setBusy(true); setError('')
    try { const result = await api.setSupport(supportProgress.intent_id, supportProgress.proposed_level); setSupportProgress(result.support_progress) }
    catch (cause) { setError(messageOf(cause)) } finally { setBusy(false) }
  }

  async function revealAlternative() {
    if (session?.status !== 'active' || !lastTurn) return
    setBusy(true); setError('')
    try { await api.exposeAlternative(lastTurn.id); setAlternativeVisible(true) }
    catch (cause) { setError(messageOf(cause)) } finally { setBusy(false) }
  }

  function startReviewVoice(item: ReviewItem) {
    if (!item.prompt_en) { setError('새 상황 질문을 불러오지 못했어요.'); return }
    reviewVoiceTarget.current = item; setError('')
    voice.speak(item.prompt_en, listen)
  }

  async function togglePause() {
    if (!session) return
    const operation = beginOperation(); setBusy(true); voice.cancel('대화를 잠시 멈췄어요.')
    try {
      if (session.status === 'paused' || pausedByLifecycle) {
        let reconciled = session
        if (pausedByLifecycle) {
          await lifecyclePause.current
          lifecyclePause.current = null
          reconciled = await api.session(session.id, operation.signal)
          if (!current(operation.epoch)) return
        }
        if (lifecycleRecoveryNeedsPause(reconciled, pausedByLifecycle)) {
          await api.pause(reconciled.id, operation.signal)
          if (!current(operation.epoch)) return
        }
        const resumed = await api.resume(session.id, operation.signal); if (!current(operation.epoch)) return; setSession(resumed); setPausedByLifecycle(false); setMedia('idle')
        const recoverySpeech = resumeSpeechText(resumed, evaluation)
        if (recoverySpeech) voice.speak(recoverySpeech, listen); else speakQuestion(resumed)
      } else { const paused=await api.pause(session.id, operation.signal); if (!current(operation.epoch)) return; setSession(paused); setMedia('paused') }
    } catch (cause) { if (!isAbort(cause) && current(operation.epoch)) setError(messageOf(cause)) } finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function skipQuestion() {
    if (!session || session.learning_state === 'retry' || session.status !== 'active') return
    const operation = beginOperation(); setBusy(true); setError(''); voice.cancel('이번 질문을 건너뛰고 있어요.')
    try {
      const raw = await api.skip(session.id, operation.signal)
      if (!current(operation.epoch)) return
      const next = 'session' in raw ? raw.session : raw
      setSession(next); setEvaluation(null); setLastTurn(null); setTranscript(''); setDraft(''); setHint(null); setSupportProgress(null); setAlternativeVisible(false)
      speakQuestion(next)
    } catch (cause) { if (!isAbort(cause) && current(operation.epoch)) setError(messageOf(cause)) }
    finally { if (current(operation.epoch)) setBusy(false) }
  }

  async function endSession() {
    if (!session) return
    const operation = beginOperation(); setBusy(true); voice.cancel('대화를 마쳤어요.')
    try {
      const result = await api.end(session.id, operation.signal); if (!current(operation.epoch)) return; setSession(result.session); setSummary(result.summary); setMedia('ended'); setPausedByLifecycle(false); localStorage.removeItem('lc_last_session'); await loadPrivate()
    } catch (cause) { if (!isAbort(cause) && current(operation.epoch)) setError(messageOf(cause)) } finally { if (current(operation.epoch)) setBusy(false) }
  }

  const authenticated = Boolean(caps?.authenticated)
  const providerReady = Boolean(caps?.tutor.available)
  const savedSession = Boolean(localStorage.getItem('lc_last_session'))

  return <div className="app-shell">
    <header className="topbar">
      <button className="brand" onClick={() => navigate('home')} aria-label="홈으로"><span className="brand-mark">LC</span><span><strong>Language Conversation</strong><small>오늘의 영어를 이어가요</small></span></button>
      <nav className="desktop-nav" aria-label="주요 메뉴">{NAV.map((item) => <NavButton key={item.id} item={item} active={page === item.id} onClick={() => navigate(item.id)} />)}</nav>
      <div className={`mode-badge ${caps?.mode === 'real' ? 'real' : 'mock'}`}><span />{caps ? (caps.mode === 'real' ? '실제 제공자' : '목업 모드') : '연결 확인 중'}</div>
    </header>

    <main className="main" id="main-content">
      {!online && <Notice tone="warning" icon={WifiOff}>오프라인이에요. 개인 기록은 기기에 임시 저장하지 않으며, 연결 후 직접 다시 시도해 주세요.</Notice>}
      {error && <Notice tone="danger">{error}<button className="text-button" onClick={() => setError('')}>닫기</button></Notice>}
      {!caps && !error ? <Loading label="PC 앱과 연결을 확인하는 중" /> : !authenticated ? <PairCard code={pairCode} setCode={setPairCode} onSubmit={pair} busy={pairBusy} /> : <>
        {page === 'home' && <HomePage caps={caps!} scenarios={scenarios} reviews={reviews} savedSession={savedSession} busy={busy} onStart={startScenario} onResume={resumeSaved} onNavigate={navigate} />}
        {page === 'conversation' && <ConversationPage caps={caps!} session={session} summary={summary} scenarios={scenarios} evaluation={evaluation} lastTurn={lastTurn} transcript={transcript} setTranscript={setTranscript} draft={draft} setDraft={setDraft} hint={hint} supportProgress={supportProgress} alternativeVisible={alternativeVisible} media={media} mediaDetail={mediaDetail} voiceName={voiceName} latency={latency} busy={busy} pausedByLifecycle={pausedByLifecycle} providerReady={providerReady} showTranslation={showTranslation} onStart={startScenario} onListen={listen} onFinish={voice.finish.bind(voice)} onSubmitText={submitText} onSaveTranscript={saveTranscript} onTranscriptSuspect={markTranscriptSuspect} onHint={requestHint} onContinue={continueAfterCorrection} onSkip={skipQuestion} onRevealAlternative={revealAlternative} onAcceptSupport={acceptSupportProposal} onReplay={() => session?.status === 'active' && session.question && voice.speak(session.question.text_en, listen)} onStopVoice={() => voice.cancel('음성 재생을 중지했어요.')} onPause={togglePause} onEnd={endSession} />}
        {page === 'review' && <ReviewPage items={reviews} results={reviewResults} setResult={(id,result)=>setReviewResults((values)=>({...values,[id]:result}))} clearResult={(id)=>setReviewResults((values)=>{const next={...values};delete next[id];return next})} reload={loadPrivate} media={media} onVoice={startReviewVoice} onFinishVoice={voice.finish.bind(voice)} />}
        {page === 'history' && <HistoryPage items={history} reload={loadPrivate} />}
        {page === 'settings' && <SettingsPage caps={caps!} showTranslation={showTranslation} setShowTranslation={setShowTranslation} minutes={sessionMinutes} setMinutes={setSessionMinutes} voiceName={voiceName} onLogout={async () => { await api.logout(); voice.cancel(); setCaps((value) => value ? { ...value, authenticated: false } : value); setSession(null) }} />}
      </>}
    </main>

    {authenticated && <nav className="mobile-nav" aria-label="주요 메뉴">{NAV.map((item) => <NavButton key={item.id} item={item} active={page === item.id} onClick={() => navigate(item.id)} />)}</nav>}
  </div>
}

function NavButton({ item, active, onClick }: { item: typeof NAV[number]; active: boolean; onClick: () => void }) { const Icon = item.icon; return <button className={active ? 'active' : ''} aria-current={active ? 'page' : undefined} onClick={onClick}><Icon size={19}/><span>{item.label}</span></button> }
function Notice({ children, tone, icon: Icon }: { children: React.ReactNode; tone: 'warning'|'danger'; icon?: typeof WifiOff }) { return <div className={`notice ${tone}`} role="alert">{Icon && <Icon size={19}/>}<span>{children}</span></div> }
function Loading({ label }: { label: string }) { return <div className="loading"><LoaderCircle className="spin"/><p>{label}</p></div> }

function PairCard({ code, setCode, onSubmit, busy }: { code: string; setCode: (v: string) => void; onSubmit: (e: React.FormEvent) => void; busy: boolean }) {
  return <section className="pair-card"><div className="eyebrow"><ShieldCheck size={15}/> 개인 기기 연결</div><h1>PC에 표시된<br/>연결 코드를 입력하세요.</h1><p>코드는 한 번만 사용할 수 있고 잠시 후 만료돼요. PC에서 새 연결 코드를 다시 발급할 수 있어요.</p><form onSubmit={onSubmit}><label htmlFor="pair-code">연결 코드</label><input id="pair-code" autoComplete="one-time-code" inputMode="text" autoCapitalize="characters" spellCheck={false} value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="예: AB12CD34" required/><button className="primary" disabled={busy || !code.trim()}>{busy ? '연결 중…' : '이 기기 연결하기'}</button></form></section>
}

function HomePage({ caps, scenarios, reviews, savedSession, busy, onStart, onResume, onNavigate }: { caps: Capabilities; scenarios: Scenario[]; reviews: ReviewItem[]; savedSession: boolean; busy: boolean; onStart: (id: string) => void; onResume: () => void; onNavigate: (page: Page) => void }) {
  return <div className="page-stack"><section className="welcome"><div><div className="eyebrow">목요일의 10분</div><h1>짧게 말해도 괜찮아요.<br/>한 문장씩 이어가 볼까요?</h1><p>틀린 곳을 찾는 시간보다, 내 표현으로 대화를 이어가는 시간을 만들어요.</p></div><div className="today-ring"><strong>{reviews.length}</strong><span>오늘 복습</span></div></section>
    {!caps.tutor.available && <Notice tone="warning">영어 튜터 제공자를 사용할 수 없어요. PC에서 로컬 모델을 시작한 뒤 연결 상태를 다시 확인해 주세요. 미리 정한 답을 실제 결과처럼 보여주지 않습니다.</Notice>}
    <section className="action-grid">{savedSession && <button className="feature-card resume" onClick={onResume} disabled={busy}><span className="round-icon"><Play size={21}/></span><span><small>저장된 대화</small><strong>이어서 연습하기</strong><em>자동으로 재개하지 않아요</em></span><ChevronRight/></button>}<button className="feature-card" onClick={() => onNavigate('review')}><span className="round-icon"><BookOpen size={21}/></span><span><small>오늘의 복습</small><strong>{reviews.length ? `${reviews.length}개 표현 다시 쓰기` : '복습 일정이 비었어요'}</strong><em>밀린 항목을 한꺼번에 강요하지 않아요</em></span><ChevronRight/></button></section>
    <section><div className="section-heading"><div><span className="eyebrow">새 대화</span><h2>오늘은 어떤 이야기를 할까요?</h2></div><span>{scenarios.length}가지 상황</span></div><div className="scenario-grid">{scenarios.map((scenario, index) => <button key={scenario.id} className="scenario-card" onClick={() => onStart(scenario.id)} disabled={busy || !caps.tutor.available}><span className="scenario-number">0{index + 1}</span><Coffee size={22}/><strong>{scenario.title_ko}</strong><p>{scenario.description_ko}</p><span className="start-link">시작하기 <ChevronRight size={16}/></span></button>)}</div>{!scenarios.length && <div className="empty">연결된 시나리오가 없어요. PC 앱의 콘텐츠 설정을 확인해 주세요.</div>}</section>
    <section className="privacy-strip"><ShieldCheck/><div><strong>개인 학습 공간</strong><p>음성과 학습 기록은 연결한 PC에서 처리합니다. 브라우저 음성은 기기 또는 브라우저 제공 방식이며 완전한 오프라인 처리를 보장하지 않아요.</p></div></section>
  </div>
}

type ConversationProps = { caps: Capabilities; session: SessionState|null; summary:SessionSummary|null; scenarios: Scenario[]; evaluation: Evaluation|null; lastTurn: Turn|null; transcript: string; setTranscript: (v:string)=>void; draft:string; setDraft:(v:string)=>void; hint:{level:string;content:string}|null; supportProgress:SupportProgress|null; alternativeVisible:boolean; media:MediaState; mediaDetail:string; voiceName:string; latency:{vadMs?:number;serverRoundTripMs?:number;ttsQueueMs?:number;totalMs?:number}; busy:boolean; pausedByLifecycle:boolean; providerReady:boolean; showTranslation:boolean; onStart:(id:string)=>void; onListen:()=>void; onFinish:()=>void; onSubmitText:()=>void; onSaveTranscript:()=>void; onTranscriptSuspect:()=>void; onHint:()=>void; onContinue:()=>void; onSkip:()=>void; onRevealAlternative:()=>void; onAcceptSupport:()=>void; onReplay:()=>void; onStopVoice:()=>void; onPause:()=>void; onEnd:()=>void }
function ConversationPage(props: ConversationProps) {
  const [hintOffer, setHintOffer] = useState(false)
  useEffect(() => { setHintOffer(false); const timer = window.setTimeout(() => setHintOffer(true), 8000); return () => clearTimeout(timer) }, [props.session?.question?.turn_id])
  if (!props.session) return <section className="empty-state"><Headphones size={34}/><h1>새 대화를 시작해 보세요.</h1><p>상황을 고르면 질문을 들려드리고, 음성 재생이 끝난 뒤 자동으로 듣기 시작해요.</p><div className="scenario-picks">{props.scenarios.map((item) => <button className="secondary" key={item.id} onClick={() => props.onStart(item.id)} disabled={!props.providerReady}>{item.title_ko}</button>)}</div></section>
  const retry = props.session.learning_state === 'retry' || props.evaluation?.correction.needed
  const question = props.session.question
  const ended = props.session.status === 'ended'
  return <div className="conversation-layout"><section className="conversation-card">
    <div className="conversation-top"><div><span className="eyebrow">{ended ? '오늘의 마무리' : props.session.learning_state === 'transfer' ? '새 상황에서 써보기' : '오늘의 대화'}</span><span className={`status-dot ${props.media}`}>{MEDIA_LABEL[props.media]}</span></div>{!ended && <div className="inline-actions"><button className="icon-button" onClick={props.onPause} aria-label={props.session.status === 'paused' ? '대화 재개' : '대화 일시정지'}>{props.session.status === 'paused' ? <Play/> : <Pause/>}</button><button className="icon-button danger" onClick={props.onEnd} aria-label="대화 끝내기"><Square/></button></div>}</div>
    {ended ? <SessionSummaryPanel summary={props.summary ?? props.session.summary ?? null}/> : <>
    <div className="question-block"><Volume2 size={22}/><div><span>TUTOR · 도움 {props.supportProgress?.current_level ?? question?.support_level ?? 'A'}</span><h1>{question?.text_en ?? (retry ? '다시 말할 준비가 되면 시작해 주세요.' : '다음 질문을 준비하고 있어요.')}</h1>{props.showTranslation && question?.translation_ko && <p className="translation">{question.translation_ko}</p>}<div className="playback-actions"><button className="text-button" onClick={props.onReplay} disabled={!question || props.media === 'speaking'}><RotateCcw size={15}/> 다시 듣기</button><button className="text-button" onClick={props.onStopVoice} disabled={props.media !== 'speaking'}><Square size={14}/> 음성 중지</button></div></div></div>
    <div className={`voice-orb ${props.media}`} aria-live="polite"><span className="orb-core">{props.media === 'listening' ? <Mic/> : props.media === 'speaking' ? <Volume2/> : props.media === 'processing' || props.media === 'transcribing' ? <LoaderCircle className="spin"/> : <Headphones/>}</span><strong>{MEDIA_LABEL[props.media]}</strong><p>{props.mediaDetail || (props.media === 'listening' ? '말을 마치고 1.5초 쉬면 자동으로 전송해요.' : props.media === 'speaking' ? '튜터가 말할 때는 마이크가 꺼져 있어요.' : '음성 또는 텍스트로 답할 수 있어요.')}</p></div>
    <div className="voice-controls">{props.media === 'listening' ? <button className="primary" onClick={props.onFinish}><Square size={18}/> 답변 완료</button> : <button className="primary" onClick={props.onListen} disabled={props.busy || props.media === 'speaking' || props.session.status === 'paused'}><Mic size={18}/> {retry ? '다시 말하기' : '말하기 시작'}</button>}</div>
    {!retry && question && props.session.status === 'active' && <button className="skip-question" onClick={props.onSkip} disabled={props.busy}>이번 질문 건너뛰기</button>}
    {props.pausedByLifecycle && <button className="resume-banner" onClick={props.onPause}><Play/> 안전하게 다시 시작하기</button>}
    <div className="text-fallback"><label htmlFor="answer">텍스트로 답하기</label><div><input id="answer" value={props.draft} onChange={(e) => props.setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') props.onSubmitText() }} placeholder="영어 문장을 입력하세요" disabled={props.busy}/><button onClick={props.onSubmitText} disabled={!props.draft.trim() || props.busy}>보내기</button></div></div>
    {question && !retry && (hintOffer || props.hint) && <div className="hint-box"><div><Lightbulb size={18}/><strong>{props.hint ? `${props.hint.level} 힌트` : '조금 더 생각해도 괜찮아요.'}</strong></div>{props.hint && <p>{props.hint.content}</p>}<button className="text-button" onClick={props.onHint} disabled={props.busy}>{props.hint ? '다음 단계 힌트 보기' : '힌트 보기'}</button>{question.task_variant === 'transfer' && <small>새 상황 연습에서는 예문을 자동으로 보여주지 않아요.</small>}</div>}
    </>}
  </section>
  {!ended && (props.lastTurn || props.evaluation) && <aside className="feedback-card"><span className="eyebrow">방금 한 말</span>{props.lastTurn && <div className="transcript-edit"><textarea aria-label="전사 수정" value={props.transcript} onChange={(e) => props.setTranscript(e.target.value)}/><div className="transcript-actions"><button className="text-button" onClick={props.onTranscriptSuspect} disabled={props.busy}>잘못 들었어요</button><button className="secondary small" onClick={props.onSaveTranscript} disabled={props.busy}>수정 후 다시 평가</button></div></div>}{props.evaluation && <>
    {props.evaluation.uncertainty_reason ? <Notice tone="warning">전사가 불확실해 평가를 보류했어요: {props.evaluation.uncertainty_reason}</Notice> : props.evaluation.correction.needed ? <div className="correction"><span>한 가지만 고쳐 볼게요</span>{props.media === 'listening' || props.media === 'preparing' ? <strong>개선 표현을 듣고, 이제 보지 않고 다시 말해 보세요.</strong> : <strong>{props.evaluation.correction.suggested_en}</strong>}<p>{props.evaluation.correction.reason_ko}</p></div> : <div className="success-note"><ShieldCheck/><div><strong>뜻이 잘 전달됐어요.</strong><p>{props.evaluation.meaning_summary_ko}</p></div></div>}
    {props.evaluation.optional_alternative && (props.alternativeVisible ? <div className="alternative-card"><span>선택한 다른 표현</span><strong>{props.evaluation.optional_alternative.text_en}</strong><p>{props.evaluation.optional_alternative.usage_ko}</p></div> : <button className="secondary alternative-reveal" onClick={props.onRevealAlternative} disabled={props.busy}>다른 표현도 하나 볼까요? <ChevronRight size={16}/></button>)}
    {retry && <div className="retry-gate"><p>다음 질문은 아직 보여주지 않아요. 최대 {props.session.max_retries}번 다시 말하거나 도움을 받고 계속할 수 있어요.</p><button className="secondary" onClick={props.onContinue} disabled={props.busy}>도움 받고 계속</button></div>}
    {props.supportProgress?.proposed_level && <div className="support-proposal"><div><strong>도움을 한 단계 줄여 볼까요?</strong><p>{supportCopy(props.supportProgress.proposed_level)} 서로 다른 질문에서 도움 없이 답한 {props.supportProgress.distinct_independent_successes}번을 바탕으로 한 제안이며, 원하면 언제든 힌트를 다시 열 수 있어요.</p></div><button className="secondary" onClick={props.onAcceptSupport} disabled={props.busy}>{props.supportProgress.proposed_level} 단계로 바꾸기</button></div>}
  </>}</aside>}
  <div className="session-meta"><span><Clock3/> 최대 45초</span><span><Volume2/> {props.voiceName}</span><span><CircleUserRound/> {props.caps.mode === 'real' ? props.caps.tutor.model || '로컬 튜터' : '목업 결과'}</span></div>
  {Object.keys(props.latency).length > 0 && <details className="latency-details"><summary>이번 음성 지연 진단</summary><div><span>마지막 음성→녹음 확정 <strong>{formatMs(props.latency.vadMs)}</strong></span><span>업로드+STT+모델 응답 <strong>{formatMs(props.latency.serverRoundTripMs)}</strong></span><span>기기 음성 대기 <strong>{formatMs(props.latency.ttsQueueMs)}</strong></span><span>음성 종료→출력 시작 <strong>{formatMs(props.latency.totalMs)}</strong></span></div><p>브라우저에서 측정한 진단값입니다. 서버가 세부 시간을 제공하지 않아 업로드·STT·모델은 합산 표시합니다.</p></details>}</div>
}

function SessionSummaryPanel({ summary }: { summary: SessionSummary | null }) {
  if (!summary) return <div className="session-summary"><h1>대화를 마쳤어요.</h1><p>요약을 불러오지 못했어요. 학습 기록에서 다시 확인해 주세요.</p></div>
  return <div className="session-summary"><h1>오늘 대화를 마쳤어요.</h1><div className="summary-counts"><span>답변 <strong>{summary.initial_answer_count}</strong></span><span>독립 성공 <strong>{summary.independent_success_count}</strong></span><span>교정 <strong>{summary.correction_count}</strong></span></div><section><span>잘한 점</span><p>{summary.strength_ko}</p></section><section><span>교정한 표현</span>{summary.corrected_expressions.length ? <ul>{summary.corrected_expressions.map((item, index) => <li key={`${item.original}-${index}`}><del>{item.original}</del><ChevronRight size={15}/><strong>{item.suggestion}</strong></li>)}</ul> : <p>이번 대화에서 저장된 교정 표현이 없어요.</p>}</section><section><span>다음 표현</span>{summary.next_expressions.length ? <ul>{summary.next_expressions.map((item) => <li key={item}><strong>{item}</strong></li>)}</ul> : <p>다음에 연습할 표현이 아직 없어요.</p>}</section></div>
}

function formatMs(value?: number) { return value == null ? '측정 중' : `${(value / 1000).toFixed(2)}초` }
export function supportCopy(level: 'A'|'B'|'C') { return level === 'A' ? '문장 뼈대를 보고 예문은 원할 때 엽니다.' : level === 'B' ? '한국어 의미 방향만 먼저 봅니다.' : '질문만 보고 내 문장을 만듭니다.' }
export function shouldPauseRestoredSession(session: SessionState) { return session.status === 'active' }
export function resumeSpeechText(session: SessionState, evaluation: Evaluation | null) { return session.learning_state === 'retry' ? evaluation?.correction.suggested_en ?? null : session.question?.text_en ?? null }
export function shouldSuspendForLifecycle(session: SessionState | null, reviewPending: boolean) { return reviewPending || session?.status === 'active' }
export function canCaptureVoice(session: SessionState | null, reviewPending: boolean) { return reviewPending || session?.status === 'active' }
export function lifecycleRecoveryNeedsPause(session: SessionState, pausedByLifecycle: boolean) { return pausedByLifecycle && session.status === 'active' }
export function shouldHeartbeat(page: Page, session: SessionState | null, visible: boolean, online: boolean, pausedByLifecycle: boolean) { return page === 'conversation' && session?.status === 'active' && visible && online && !pausedByLifecycle }

function ReviewPage({ items, results, setResult, clearResult, reload, media, onVoice, onFinishVoice }: { items: ReviewItem[]; results:Record<string,ReviewResult>; setResult:(id:string,result:ReviewResult)=>void; clearResult:(id:string)=>void; reload: () => Promise<void>; media: MediaState; onVoice:(item:ReviewItem)=>void; onFinishVoice:()=>void }) {
  const [drafts, setDrafts] = useState<Record<string,string>>({}); const [message, setMessage] = useState(''); const [hints,setHints]=useState<Record<string,{level:string;content:string}>>({}); const [alternatives,setAlternatives]=useState<Record<string,boolean>>({})
  async function submit(item: ReviewItem) { try { const result=await api.reviewAttempt(item.id, drafts[item.id] ?? '', item.task_variant ?? 'transfer'); setResult(item.id,result); setMessage(result.evaluation.correction.needed ? '핵심 교정을 확인하고 같은 카드에서 다시 말해 보세요.' : result.evaluation.uncertainty_reason ? '평가를 보류했어요. 전사를 확인하고 다시 답해 주세요.' : '뜻이 잘 전달됐어요. 도움 수준과 복습 일정은 서버 기록으로 계산합니다.') } catch (e) { setMessage(messageOf(e)) } }
  async function requestReviewHint(item:ReviewItem) { try { const result=await api.reviewHint(item.id); setHints({...hints,[item.id]:result.hint}) } catch(e) { setMessage(messageOf(e)) } }
  async function revealReviewAlternative(item:ReviewItem) { try { await api.exposeReviewAlternative(item.id); setAlternatives({...alternatives,[item.id]:true}) } catch(e) { setMessage(messageOf(e)) } }
  async function finishItem(item:ReviewItem) { clearResult(item.id); await reload() }
  return <div className="page-stack"><div className="page-title"><span className="eyebrow">간격 복습</span><h1>오늘 다시 써볼 표현</h1><p>답을 먼저 보여주지 않는 새 질문에서 내 문장으로 사용해 보세요.</p></div>{message && <div className="notice warning" role="status">{message}</div>}<div className="review-list">{items.map((item, index) => { const result=results[item.id]; const evaluation=result?.evaluation; const resultItem=result?.item ?? item; const retryEnded=resultItem.max_retries != null && (resultItem.retry_count ?? 0)>=resultItem.max_retries; return <article className="review-card" key={item.id}><span className="review-count">{index + 1} / {items.length}</span><h2>{item.prompt_en ?? item.question_en ?? '새 상황 질문을 불러오지 못했어요.'}</h2>{hints[item.id] && <div className="hint-box"><strong>{hints[item.id].level} 힌트</strong><p>{hints[item.id].content}</p></div>}{evaluation && <div className="review-feedback">{evaluation.uncertainty_reason ? <Notice tone="warning">평가 보류: {evaluation.uncertainty_reason}</Notice> : evaluation.correction.needed ? <div className="correction"><span>한 가지만 고쳐 볼게요</span><strong>{evaluation.correction.suggested_en}</strong><p>{evaluation.correction.reason_ko}</p></div> : <div className="success-note"><ShieldCheck/><div><strong>뜻이 잘 전달됐어요.</strong><p>{evaluation.meaning_summary_ko}</p></div></div>}{evaluation.optional_alternative && (alternatives[item.id] ? <div className="alternative-card"><span>선택한 다른 표현</span><strong>{evaluation.optional_alternative.text_en}</strong><p>{evaluation.optional_alternative.usage_ko}</p></div> : <button className="secondary alternative-reveal" onClick={()=>revealReviewAlternative(item)}>다른 표현도 하나 볼까요?</button>)}</div>}<label htmlFor={`review-${item.id}`}>{evaluation?.correction.needed ? '다시 답하기' : '내 답변'}</label><textarea id={`review-${item.id}`} value={drafts[item.id] ?? ''} onChange={(e) => setDrafts({ ...drafts, [item.id]: e.target.value })}/><div className="review-actions"><button className="secondary" onClick={()=>requestReviewHint(item)}>단계별 힌트</button><button className="secondary" disabled={retryEnded || media==='speaking' || media==='processing'} onClick={()=>media === 'listening' ? onFinishVoice() : onVoice(item)}>{media === 'listening' ? <><Square size={16}/> 답변 완료</> : <><Mic size={16}/> 음성으로 답하기</>}</button><button className="primary" disabled={retryEnded || !drafts[item.id]?.trim()} onClick={() => submit(item)}>{evaluation?.correction.needed ? '다시 확인' : '텍스트 답변 확인'}</button></div>{evaluation && !evaluation.correction.needed && !evaluation.uncertainty_reason && <button className="primary review-next" onClick={()=>finishItem(item)}>다음 복습으로</button>}{retryEnded && <div className="retry-gate"><p>오늘 재시도는 여기까지예요. 이 카드는 다음 학습에서도 다시 만날 수 있어요.</p><button className="secondary" onClick={()=>finishItem(item)}>다음 복습으로</button></div>}</article>})}</div>{!items.length && <div className="empty-state"><BookOpen/><h2>오늘 예정된 복습이 없어요.</h2><p>다음 학습에서 새 표현을 쌓아 보세요.</p></div>}</div>
}

function HistoryPage({ items, reload }: { items: HistorySession[]; reload: () => Promise<void> }) {
  const [message, setMessage] = useState('')
  async function remove(item: HistorySession) { if (!confirm('이 세션의 전사, 교정, 자동 복습 카드를 삭제할까요? 별도로 내보낸 파일은 삭제되지 않습니다.')) return; try { await api.deleteSession(item.id); setMessage('세션을 삭제했어요.'); await reload() } catch (e) { setMessage(messageOf(e)) } }
  return <div className="page-stack"><div className="page-title"><span className="eyebrow">학습 기록</span><h1>내가 이어 온 대화</h1><p>점수보다 실제로 말한 표현과 교정 기록을 확인해요.</p></div>{message && <div className="notice warning">{message}</div>}<div className="history-list">{items.map((item) => { const summary=typeof item.summary === 'object' ? item.summary : null; return <article className="history-row" key={item.id}><div className="history-icon"><MessageCircle/></div><div><span>{item.ended_at ? new Date(item.ended_at).toLocaleDateString('ko-KR') : '진행 중'}</span><h2>{item.scenario_id}</h2><p>{typeof item.summary === 'string' ? item.summary : summary?.strength_ko ?? '저장된 대화를 나중에 다시 확인할 수 있어요.'}</p>{summary && <small>답변 {summary.initial_answer_count} · 독립 성공 {summary.independent_success_count} · 교정 {summary.correction_count}{summary.next_expressions.length ? ` · 다음: ${summary.next_expressions.join(' · ')}` : ''}</small>}</div><button className="icon-button danger" aria-label="이 세션 삭제" onClick={() => remove(item)}><Trash2/></button></article> })}</div>{!items.length && <div className="empty">아직 저장된 대화가 없어요.</div>}</div>
}

function SettingsPage({ caps, showTranslation, setShowTranslation, minutes, setMinutes, voiceName, onLogout }: { caps: Capabilities; showTranslation:boolean; setShowTranslation:(v:boolean)=>void; minutes:number; setMinutes:(v:number)=>void; voiceName:string; onLogout:()=>void }) {
  const [message, setMessage] = useState(''); const fileRef = useRef<HTMLInputElement>(null); const previewRef = useRef('')
  async function exportData() { try { const data = await api.exportData(); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type:'application/json' })); const anchor = document.createElement('a'); anchor.href=url; anchor.download=`language-conversation-${new Date().toISOString().slice(0,10)}.json`; anchor.click(); URL.revokeObjectURL(url); setMessage('내보내기 파일을 만들었어요.') } catch(e) { setMessage(messageOf(e)) } }
  async function preview(file?: File) { if (!file) return; try { const data = JSON.parse(await file.text()); const result = await api.importPreview(data); previewRef.current=result.preview_id; setMessage(`가져오기 미리보기: ${Object.entries(result.counts).map(([k,v])=>`${k} ${v}`).join(', ')}${result.warnings.length ? ` · 주의: ${result.warnings.join(' / ')}` : ''}`) } catch(e) { setMessage(`파일을 확인하지 못했어요. ${messageOf(e)}`) } }
  async function applyImport() { if (!previewRef.current) return; try { await api.importApply(previewRef.current); previewRef.current=''; setMessage('미리 본 데이터를 가져왔어요.') } catch(e) { setMessage(messageOf(e)) } }
  async function deleteAll() { if (!confirm('모든 학습 기록과 이 기기의 앱 캐시를 삭제할까요? 별도로 내보낸 파일은 남습니다.')) return; try { await api.deleteAll(); localStorage.clear(); const keys = await caches.keys(); await Promise.all(keys.map((key)=>caches.delete(key))); setMessage('학습 데이터와 이 기기의 캐시를 삭제했어요.') } catch(e) { setMessage(messageOf(e)) } }
  return <div className="page-stack"><div className="page-title"><span className="eyebrow">내 환경</span><h1>설정</h1><p>학습 방식과 데이터 보관 범위를 직접 관리해요.</p></div>{message && <div className="notice warning" role="status">{message}{previewRef.current && <button className="text-button" onClick={applyImport}>이 내용 적용</button>}</div>}
    <section className="settings-card"><h2>학습 기본값</h2><label className="setting-row"><span><strong>번역 펼쳐 보기</strong><small>한국어 번역이 제공된 질문에서만 함께 표시합니다.</small></span><input type="checkbox" checked={showTranslation} onChange={(e)=>setShowTranslation(e.target.checked)}/></label><label className="setting-row"><span><strong>세션 안내 시간</strong><small>강제 종료 시간이 아닌 안내 기준입니다.</small></span><select value={minutes} onChange={(e)=>setMinutes(Number(e.target.value))}><option value="5">5분</option><option value="10">10분</option><option value="15">15분</option></select></label></section>
    <section className="settings-card"><h2>음성과 실행 환경</h2><dl className="facts"><div><dt>튜터</dt><dd>{caps.mode === 'real' ? `실제 · ${caps.tutor.model ?? '로컬 모델'}` : '목업 · 학습 검증용'}</dd></div><div><dt>음성 인식</dt><dd>{caps.speech_input.available ? `${caps.speech_input.adapter} · 최대 ${caps.speech_input.max_seconds}초` : '사용할 수 없음'}</dd></div><div><dt>음성 출력</dt><dd>{voiceName}</dd></div><div><dt>데이터 시간대</dt><dd>{caps.timezone}</dd></div></dl><p className="fine-print">필수 유료 API로 자동 전환하지 않습니다. 브라우저 음성 처리 위치는 기기와 브라우저 구현에 따라 달라질 수 있어요.</p></section>
    <section className="settings-card"><h2>내 데이터</h2><div className="button-list"><button className="secondary" onClick={exportData}><Download/> JSON으로 내보내기</button><button className="secondary" onClick={()=>fileRef.current?.click()}><Upload/> 가져올 파일 미리보기</button><input ref={fileRef} hidden type="file" accept="application/json" onChange={(e)=>preview(e.target.files?.[0])}/><button className="secondary" onClick={onLogout}><LogOut/> 이 기기 연결 해제</button><button className="destructive" onClick={deleteAll}><Trash2/> 모든 학습 데이터 삭제</button></div></section>
  </div>
}
