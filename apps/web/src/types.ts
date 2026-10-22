export type MediaState = 'idle' | 'preparing' | 'listening' | 'transcribing' | 'processing' | 'speaking' | 'paused' | 'reconnecting' | 'ended' | 'error'
export type LearningState = 'warmup' | 'question' | 'feedback' | 'retry' | 'variation' | 'transfer' | 'summary' | 'complete'
export type TaskVariant = 'initial' | 'variation' | 'transfer' | 'review'

export interface Capabilities {
  mode: 'real' | 'mock'
  tutor: { available: boolean; model: string | null }
  speech_input: { available: boolean; adapter: string; max_seconds: number; accepted_mime_types: string[] }
  timezone: string
  authenticated: boolean
}

export interface Scenario {
  id: string
  version: string
  title_ko: string
  description_ko: string
  intents: Array<{ id: string; label_ko?: string; name?: string } | string>
}

export interface Question {
  turn_id: string
  text_en: string
  intent_id: string
  task_variant: TaskVariant
  translation_ko?: string | null
  support_level?: SupportLevel
}

export type SupportLevel = 'A'|'B'|'C'
export interface SupportProgress {
  intent_id: string
  current_level: SupportLevel
  proposed_level: SupportLevel | null
  distinct_independent_successes: number
}

export interface SessionState {
  id: string
  scenario_id: string
  status: 'active' | 'paused' | 'ended'
  learning_state: LearningState
  media_state: MediaState
  question: Question | null
  pending_next_question: boolean
  retry_count: number
  max_retries: number
  response_revision: number
  updated_at: string
  heartbeat_expires_at?: string | null
  summary?: SessionSummary | null
  current_turn?: Turn | null
  current_evaluation?: Evaluation | null
}

export interface Evaluation {
  schema_version: string
  turn_id: string
  transcript_version: number
  evaluation_status: string
  intent_achieved: boolean
  meaning_summary_ko: string | null
  correction: { needed: boolean; original_span: string | null; suggested_en: string | null; reason_ko: string | null }
  optional_alternative: { text_en: string; usage_ko: string } | null
  retry_prompt_en: string | null
  next_question_en: string | null
  uncertainty_reason: string | null
  assistance_level?: string
  outcome?: string
}

export interface Turn {
  id: string
  final_text?: string
  text?: string
  transcript_version: number
}

export interface TurnResult { turn: Turn; evaluation: Evaluation; session: SessionState; support_progress?: SupportProgress | null }

export interface SessionSummary {
  strength_ko: string
  initial_answer_count: number
  independent_success_count: number
  correction_count: number
  corrected_expressions: Array<{ original: string; suggestion: string }>
  next_expressions: string[]
}

export interface ReviewItem {
  id: string
  prompt_en?: string
  question_en?: string
  expression?: string
  canonical_form?: string
  due_at?: string
  stage?: number
  task_variant?: 'transfer'
  retry_count?: number
  max_retries?: number
}

export interface ReviewResult { attempt: unknown; item: ReviewItem; evaluation: Evaluation }

export interface HistorySession {
  id: string
  scenario_id: string
  status: string
  started_at?: string
  ended_at?: string
  summary?: SessionSummary | string | null
}

export interface ApiErrorBody { detail?: { code?: string; message?: string; retryable?: boolean } | string }
