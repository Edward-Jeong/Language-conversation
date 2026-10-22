from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Correction(StrictModel):
    needed: bool
    original_span: str | None = Field(max_length=160)
    suggested_en: str | None = Field(max_length=240)
    reason_ko: str | None = Field(max_length=240)

    @model_validator(mode="after")
    def consistent(self) -> "Correction":
        details = (self.original_span, self.suggested_en, self.reason_ko)
        if self.needed and any(value is None or not value.strip() for value in details):
            raise ValueError("a needed correction requires one grounded span, suggestion, and reason")
        if not self.needed and any(value is not None for value in details):
            raise ValueError("an unneeded correction must not contain correction details")
        return self


class OptionalAlternative(StrictModel):
    text_en: str = Field(min_length=1, max_length=240)
    usage_ko: str = Field(min_length=1, max_length=240)


class TutorEvaluation(StrictModel):
    schema_version: Literal["1"]
    turn_id: str = Field(min_length=1, max_length=64)
    transcript_version: int = Field(ge=1)
    evaluation_status: Literal["ready", "unassessable"]
    intent_achieved: bool | None
    meaning_summary_ko: str | None = Field(default=None, max_length=240)
    correction: Correction
    optional_alternative: OptionalAlternative | None
    retry_prompt_en: str | None = Field(default=None, max_length=240)
    next_question_en: str | None = Field(default=None, max_length=240)
    uncertainty_reason: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def status_consistency(self) -> "TutorEvaluation":
        if self.evaluation_status == "unassessable":
            if self.intent_achieved is not None or not self.uncertainty_reason:
                raise ValueError("unassessable output needs null intent and an uncertainty reason")
            if self.correction.needed:
                raise ValueError("unassessable output cannot require a correction")
        elif self.intent_achieved is None:
            raise ValueError("ready output needs an intent decision")
        return self


class PairRequest(StrictModel):
    code: str = Field(min_length=6, max_length=12, pattern=r"^[A-Z0-9]+$")


class CreateSessionRequest(StrictModel):
    scenario_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128)


class SubmitTurnRequest(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    client_attempt_id: str = Field(min_length=8, max_length=128)
    intent_id: str | None = Field(default=None, max_length=64)
    task_variant: Literal["initial", "variation", "transfer"] | None = None


class ReviseTurnRequest(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    client_attempt_id: str = Field(min_length=8, max_length=128)


class AttemptRequest(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    client_attempt_id: str = Field(min_length=8, max_length=128)


class ActionRequest(StrictModel):
    client_attempt_id: str = Field(min_length=8, max_length=128)


class ReviewAttemptRequest(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    client_attempt_id: str = Field(min_length=8, max_length=128)
    task_variant: Literal["review", "transfer"] = "review"


class ImportApplyRequest(StrictModel):
    preview_id: str = Field(min_length=8, max_length=64)
    client_attempt_id: str = Field(min_length=8, max_length=128)


class SupportPreferenceRequest(StrictModel):
    level: Literal["A", "B", "C"]
    client_attempt_id: str = Field(min_length=8, max_length=128)


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class APIErrorBody(BaseModel):
    detail: ErrorDetail


class Event(BaseModel):
    event_id: str
    session_id: str
    turn_id: str | None
    client_attempt_id: str | None
    event_type: str
    sequence: int
    occurred_at: datetime
    transcript_version: int | None
    response_revision: int
    payload: dict[str, Any]
