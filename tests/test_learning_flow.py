from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("LC_AUTH_SECRET", "test-secret-0123456789-0123456789")
os.environ.setdefault("LC_AI_PROVIDER", "mock")

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.models import Correction, OptionalAlternative, TutorEvaluation
from apps.api.security import issue_pairing_code


def ready(context: dict[str, Any], *, correction: bool = False, achieved: bool = True) -> TutorEvaluation:
    span = "go" if correction else None
    return TutorEvaluation(
        schema_version="1", turn_id=context["turn_id"], transcript_version=context["transcript_version"],
        evaluation_status="ready", intent_achieved=achieved, meaning_summary_ko="의도를 전달함",
        correction=Correction(needed=correction,original_span=span,suggested_en="went" if correction else None,reason_ko="과거형을 사용해요." if correction else None),
        optional_alternative=None,retry_prompt_en="Please try again." if correction else None,
        next_question_en="What happened next?",uncertainty_reason=None,
    )


class ScriptedTutor:
    model_id = "scripted-test"
    def __init__(self, scripts: list[Callable[[dict[str,Any]],TutorEvaluation]] | None = None): self.scripts=deque(scripts or [])
    async def available(self) -> bool: return True
    async def evaluate(self, context: dict[str,Any]) -> TutorEvaluation:
        return (self.scripts.popleft() if self.scripts else ready)(context)


class GateTutor(ScriptedTutor):
    def __init__(self) -> None:
        super().__init__(); self.started=asyncio.Event(); self.release=asyncio.Event(); self.cancelled=asyncio.Event()
    async def evaluate(self, context: dict[str,Any]) -> TutorEvaluation:
        self.started.set()
        try: await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set(); raise
        return ready(context)


class FailingTutor(ScriptedTutor):
    async def evaluate(self, context: dict[str,Any]) -> TutorEvaluation:
        from apps.api.providers import ProviderUnavailable
        raise ProviderUnavailable("offline")


class CountingTutor(ScriptedTutor):
    def __init__(self) -> None: super().__init__(); self.calls=0
    async def evaluate(self, context: dict[str,Any]) -> TutorEvaluation:
        self.calls+=1
        return ready(context)


class CountingWhisper:
    allowed_mime_types={"audio/wav":".wav"}
    def __init__(self, *, gated: bool=False) -> None:
        self.calls=0; self.started=threading.Event(); self.release=threading.Event()
        if not gated: self.release.set()
    async def available(self) -> bool: return True
    async def transcribe(self,data: bytes,mime_type: str) -> tuple[str,int]:
        self.calls+=1; self.started.set(); await asyncio.to_thread(self.release.wait)
        return "I stayed home.",1


def settings(tmp_path: Path) -> Settings:
    return Settings(database_path=tmp_path/"learning.db",ai_provider="mock",ollama_url="http://127.0.0.1:11434",ollama_model="gemma3:4b",whisper_url="http://127.0.0.1:8080/inference",cookie_secure=True,auth_secret="test-secret-0123456789-0123456789",allowed_origins=("https://testserver",))


def client_for(tmp_path: Path, tutor: ScriptedTutor) -> tuple[TestClient,Any,dict[str,str]]:
    application=create_app(settings=settings(tmp_path),tutor=tutor); client=TestClient(application,base_url="https://testserver")
    code,_=issue_pairing_code(application.state.db,application.state.settings)
    paired=client.post("/api/auth/pair",json={"code":code},headers={"Origin":"https://testserver"}); assert paired.status_code==200
    return client,application,{"Origin":"https://testserver","X-CSRF-Token":paired.json()["csrf_token"]}


def client_for_audio(tmp_path: Path,tutor: ScriptedTutor,whisper: CountingWhisper) -> tuple[TestClient,Any,dict[str,str]]:
    application=create_app(settings=settings(tmp_path),tutor=tutor,whisper_provider=whisper); client=TestClient(application,base_url="https://testserver")
    code,_=issue_pairing_code(application.state.db,application.state.settings)
    paired=client.post("/api/auth/pair",json={"code":code},headers={"Origin":"https://testserver"}); assert paired.status_code==200
    return client,application,{"Origin":"https://testserver","X-CSRF-Token":paired.json()["csrf_token"]}


def create_session(client: TestClient, headers: dict[str,str], key: str="create-session-001") -> dict[str,Any]:
    response=client.post("/api/sessions",json={"scenario_id":"weekend-story","idempotency_key":key},headers=headers); assert response.status_code==200; return response.json()


def test_correction_withholds_question_until_retry_and_is_idempotent(tmp_path: Path) -> None:
    tutor=ScriptedTutor([lambda c:ready(c,correction=True),lambda c:ready(c)])
    client,_,headers=client_for(tmp_path,tutor); session=create_session(client,headers)
    question_id=session["question"]["turn_id"]
    first=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I go yesterday.","client_attempt_id":"answer-attempt-0001"},headers=headers)
    assert first.status_code==200
    body=first.json(); assert body["evaluation"]["next_question_en"] is None; assert body["session"]["learning_state"]=="retry"; assert body["session"]["pending_next_question"] is True
    resumed=client.get(f"/api/sessions/{session['id']}").json(); assert resumed["current_turn"]["id"]==body["turn"]["id"]; assert resumed["current_evaluation"]["next_question_en"] is None
    duplicate=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I go yesterday.","client_attempt_id":"answer-attempt-0001"},headers=headers)
    assert duplicate.json()==body
    blocked=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"another answer","client_attempt_id":"answer-attempt-0002"},headers=headers)
    assert blocked.status_code==409; assert blocked.json()["detail"]["code"]=="retry_required"
    retried=client.post(f"/api/turns/{body['turn']['id']}/retry",json={"text":"I went yesterday.","client_attempt_id":"retry-attempt-0001"},headers=headers)
    assert retried.status_code==200; assert retried.json()["session"]["learning_state"]=="question"; assert retried.json()["session"]["question"]["turn_id"]!=question_id


def test_audio_authorizes_session_before_transcription(tmp_path: Path) -> None:
    whisper=CountingWhisper(); client,application,headers=client_for_audio(tmp_path,CountingTutor(),whisper); session=create_session(client,headers)
    paused=client.post(f"/api/sessions/{session['id']}/pause",json={"client_attempt_id":"pause-before-audio-001"},headers=headers); assert paused.status_code==200
    response=client.post(f"/api/sessions/{session['id']}/audio",files={"file":("speech.wav",b"fake","audio/wav")},data={"client_attempt_id":"paused-audio-0001"},headers=headers)
    assert response.status_code==409 and response.json()["detail"]["code"]=="session_not_active"; assert whisper.calls==0
    with application.state.db.transaction(immediate=True) as connection:
        connection.execute("INSERT INTO users(id,locale,timezone,created_at) VALUES ('other-user','ko-KR','Asia/Seoul','2026-01-01T00:00:00+00:00')")
        connection.execute("UPDATE sessions SET user_id='other-user' WHERE id=?",(session["id"],))
    response=client.post(f"/api/sessions/{session['id']}/audio",files={"file":("speech.wav",b"fake","audio/wav")},data={"client_attempt_id":"foreign-audio-001"},headers=headers)
    assert response.status_code==404 and whisper.calls==0
    response=client.post("/api/reviews/not-owned/audio",files={"file":("speech.wav",b"fake","audio/wav")},data={"client_attempt_id":"foreign-review-audio-001"},headers=headers)
    assert response.status_code==404 and whisper.calls==0


def test_audio_transcript_is_rejected_if_pause_resume_changes_lease(tmp_path: Path) -> None:
    tutor=CountingTutor(); whisper=CountingWhisper(gated=True); client,_,headers=client_for_audio(tmp_path,tutor,whisper); session=create_session(client,headers)
    result:list[Any]=[]
    def upload() -> None:
        result.append(client.post(f"/api/sessions/{session['id']}/audio",files={"file":("speech.wav",b"fake","audio/wav")},data={"client_attempt_id":"stale-audio-0001"},headers=headers))
    worker=threading.Thread(target=upload); worker.start()
    assert whisper.started.wait(timeout=5)
    try:
        assert client.post(f"/api/sessions/{session['id']}/pause",json={"client_attempt_id":"pause-mid-audio-001"},headers=headers).status_code==200
        assert client.post(f"/api/sessions/{session['id']}/resume",json={"client_attempt_id":"resume-mid-audio-001"},headers=headers).status_code==200
    finally:
        whisper.release.set(); worker.join(timeout=5)
    assert not worker.is_alive() and result
    assert result[0].status_code==409 and result[0].json()["detail"]["code"]=="input_cancelled"
    assert whisper.calls==1 and tutor.calls==0


def test_pause_cancels_all_concurrent_audio_inputs(tmp_path: Path) -> None:
    tutor=CountingTutor(); whisper=CountingWhisper(gated=True); client,application,headers=client_for_audio(tmp_path,tutor,whisper); session=create_session(client,headers,"multi-audio-create")
    results:list[Any]=[]
    def upload(attempt: str) -> None: results.append(client.post(f"/api/sessions/{session['id']}/audio",files={"file":("speech.wav",b"fake","audio/wav")},data={"client_attempt_id":attempt},headers=headers))
    workers=[threading.Thread(target=upload,args=(f"multi-audio-{index}",)) for index in (1,2)]
    for worker in workers: worker.start()
    assert whisper.started.wait(timeout=5)
    deadline=time.time()+5
    while time.time()<deadline:
        with application.state.orchestrator._generation_lock: registered=len(application.state.orchestrator._input_tasks.get(session["id"],set()))
        if registered==2: break
        time.sleep(0.01)
    assert registered==2
    try: assert client.post(f"/api/sessions/{session['id']}/pause",json={"client_attempt_id":"multi-audio-pause"},headers=headers).status_code==200
    finally:
        whisper.release.set()
        for worker in workers: worker.join(timeout=5)
    assert len(results)==2 and all(response.status_code==409 and response.json()["detail"]["code"]=="input_cancelled" for response in results); assert tutor.calls==0


def test_heartbeat_expiry_pauses_and_requires_explicit_resume(tmp_path: Path) -> None:
    client,application,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers,"heartbeat-create-001")
    pulse=client.post(f"/api/sessions/{session['id']}/heartbeat",json={"client_attempt_id":"heartbeat-pulse-001"},headers=headers)
    assert pulse.status_code==200 and pulse.json()["ok"] is True and pulse.json()["expires_at"]
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE sessions SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?",(session["id"],))
    expired=client.get(f"/api/sessions/{session['id']}"); assert expired.status_code==200 and expired.json()["status"]=="paused"
    rejected=client.post(f"/api/sessions/{session['id']}/heartbeat",json={"client_attempt_id":"heartbeat-pulse-002"},headers=headers)
    assert rejected.status_code==409 and rejected.json()["detail"]["code"]=="heartbeat_expired"
    resumed=client.post(f"/api/sessions/{session['id']}/resume",json={"client_attempt_id":"heartbeat-resume-001"},headers=headers)
    assert resumed.status_code==200 and resumed.json()["status"]=="active" and resumed.json()["heartbeat_expires_at"]
    with application.state.db.connect() as connection: assert connection.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND event_type='session_heartbeat_expired'",(session["id"],)).fetchone()[0]==1


def test_heartbeat_expiry_cancels_inflight_transcription(tmp_path: Path) -> None:
    tutor=CountingTutor(); whisper=CountingWhisper(gated=True); client,application,headers=client_for_audio(tmp_path,tutor,whisper); session=create_session(client,headers,"heartbeat-audio-create")
    result:list[Any]=[]
    worker=threading.Thread(target=lambda:result.append(client.post(f"/api/sessions/{session['id']}/audio",files={"file":("speech.wav",b"fake","audio/wav")},data={"client_attempt_id":"heartbeat-audio-input"},headers=headers))); worker.start(); assert whisper.started.wait(timeout=5)
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE sessions SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?",(session["id"],))
    try: assert client.get(f"/api/sessions/{session['id']}").json()["status"]=="paused"
    finally: whisper.release.set(); worker.join(timeout=5)
    assert result and result[0].status_code==409 and result[0].json()["detail"]["code"]=="input_cancelled" and tutor.calls==0


def test_zero_answer_end_summary_is_factual_and_restorable(tmp_path: Path) -> None:
    client,_,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers,"zero-answer-create")
    ended=client.post(f"/api/sessions/{session['id']}/end",json={"client_attempt_id":"zero-answer-end"},headers=headers)
    assert ended.status_code==200; summary=ended.json()["summary"]
    assert summary["initial_answer_count"]==0 and summary["independent_success_count"]==0 and summary["corrected_expressions"]==[] and "0개" in summary["strength_ko"]
    restored=client.get(f"/api/sessions/{session['id']}").json(); assert restored["summary"]==summary


def test_hint_exposure_controls_assistance_and_client_cannot_spoof_transfer(tmp_path: Path) -> None:
    client,_,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers)
    spoof=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"spoof-attempt-0001","task_variant":"transfer"},headers=headers)
    assert spoof.status_code==409; assert spoof.json()["detail"]["code"]=="task_variant_mismatch"
    question_id=session["question"]["turn_id"]
    for index,expected in enumerate(("H1","H2","H3"),1):
        hint=client.post(f"/api/turns/{question_id}/hints",json={"client_attempt_id":f"hint-attempt-000{index}"},headers=headers)
        assert hint.status_code==200; assert hint.json()["hint"]["level"]==expected
        if index==1:
            duplicate=client.post(f"/api/turns/{question_id}/hints",json={"client_attempt_id":"hint-attempt-0001"},headers=headers); assert duplicate.json()["hint"]["level"]=="H1"
    answer=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"answer-attempt-0003"},headers=headers)
    assert answer.json()["evaluation"]["assistance_level"]=="model_imitation"
    with client.app.state.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_items").fetchone()[0]==0


def test_transcript_revision_invalidates_prior_evaluation_and_rebuilds_review(tmp_path: Path) -> None:
    client,application,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers)
    first=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"answer-attempt-0004"},headers=headers).json()
    revised=client.patch(f"/api/turns/{first['turn']['id']}",json={"text":"I relaxed at home.","client_attempt_id":"revise-attempt-001"},headers=headers)
    assert revised.status_code==200; assert revised.json()["turn"]["transcript_version"]==2
    with application.state.db.connect() as connection:
        rows=connection.execute("SELECT transcript_version,invalidated_at FROM evaluations WHERE turn_id=? ORDER BY transcript_version",(first["turn"]["id"],)).fetchall()
        assert len(rows)==2; assert rows[0]["invalidated_at"] is not None; assert rows[1]["invalidated_at"] is None
        expression=connection.execute("SELECT canonical_form FROM expressions WHERE source_turn_id=?",(first["turn"]["id"],)).fetchone()
        assert expression["canonical_form"]=="I relaxed at home."


def test_review_uses_server_hint_state_and_advances_at_most_once_per_day(tmp_path: Path) -> None:
    client,application,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers)
    client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"answer-attempt-0005"},headers=headers)
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE review_items SET due_at='2000-01-01T00:00:00+00:00'")
    item=client.get("/api/reviews/due",params={"limit":3}).json()["items"][0]
    assert "canonical_form" not in item and item["prompt_en"]
    hint=client.post(f"/api/reviews/{item['id']}/hints",json={"client_attempt_id":"review-hint-0001"},headers=headers); assert hint.json()["hint"]["level"]=="H1"
    supported=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I stayed home.","client_attempt_id":"review-attempt-0001","task_variant":"transfer"},headers=headers).json()
    assert supported["attempt"]["assistance_level"]=="supported_production"; assert supported["item"]["stage"]==0; assert supported["item"]["retry_count"]==0 and supported["item"]["max_retries"]==2
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE review_items SET due_at='2000-01-01T00:00:00+00:00' WHERE id=?",(item["id"],))
    retried=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I stayed home.","client_attempt_id":"review-attempt-0002","task_variant":"review"},headers=headers).json()
    assert retried["attempt"]["assistance_level"]=="supported_production"; assert retried["item"]["stage"]==0 and retried["item"]["retry_count"]==1
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE review_items SET due_at='2000-01-01T00:00:00+00:00' WHERE id=?",(item["id"],))
    same_day=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I stayed home.","client_attempt_id":"review-attempt-0003","task_variant":"transfer"},headers=headers).json()
    assert same_day["item"]["stage"]==0 and same_day["item"]["retry_count"]==2
    limited=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I stayed home.","client_attempt_id":"review-attempt-0004"},headers=headers)
    assert limited.status_code==409 and limited.json()["detail"]["code"]=="review_retry_limit_reached"
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE review_items SET due_at='2000-01-01T00:00:00+00:00',activity_learning_date='2000-01-01',attempt_count=0,current_hint_level='H0' WHERE id=?",(item["id"],))
    next_day=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I stayed home.","client_attempt_id":"review-attempt-0005"},headers=headers).json()
    assert next_day["attempt"]["assistance_level"]=="independent_production" and next_day["item"]["stage"]==1


def test_review_correction_and_alternative_are_audited_before_retry(tmp_path: Path) -> None:
    def correction_with_alternative(context: dict[str,Any]) -> TutorEvaluation:
        return TutorEvaluation(schema_version="1",turn_id=context["turn_id"],transcript_version=1,evaluation_status="ready",intent_achieved=True,meaning_summary_ko="어제 한 일을 말함",correction=Correction(needed=True,original_span="go",suggested_en="went",reason_ko="과거형을 사용해요."),optional_alternative=OptionalAlternative(text_en="I relaxed at home.",usage_ko="집에서 쉰 일을 말할 때"),retry_prompt_en="Please try again with 'went'.",next_question_en=None,uncertainty_reason=None)
    tutor=ScriptedTutor([lambda c:ready(c),correction_with_alternative,lambda c:ready(c),lambda c:ready(c)]); client,application,headers=client_for(tmp_path,tutor); session=create_session(client,headers,"review-exposure-create")
    client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"review-source-answer"},headers=headers)
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE review_items SET due_at='2000-01-01T00:00:00+00:00'")
    item=client.get("/api/reviews/due").json()["items"][0]
    first=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I go yesterday.","client_attempt_id":"review-correction-001"},headers=headers)
    assert first.status_code==200; first_body=first.json(); assert first_body["attempt"]["assistance_level"]=="novel_context_transfer"; assert first_body["evaluation"]["correction"]["suggested_en"]=="went"; assert first_body["item"]["current_hint_level"]=="H3"
    exposed=client.post(f"/api/reviews/{item['id']}/alternative-exposure",json={"client_attempt_id":"review-alt-exposure-001"},headers=headers)
    assert exposed.status_code==200 and exposed.json()["alternative"]["text_en"]=="I relaxed at home."
    duplicate=client.post(f"/api/reviews/{item['id']}/alternative-exposure",json={"client_attempt_id":"review-alt-exposure-001"},headers=headers); assert duplicate.json()==exposed.json()
    retried=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I went yesterday.","client_attempt_id":"review-correction-retry-001"},headers=headers)
    assert retried.status_code==200; assert retried.json()["attempt"]["assistance_level"]=="model_imitation"; assert retried.json()["item"]["stage"]==0 and retried.json()["item"]["retry_count"]==1
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE review_items SET due_at='2000-01-01T00:00:00+00:00',activity_learning_date='2000-01-01',attempt_count=0,current_hint_level='H0' WHERE id=?",(item["id"],))
    repeated=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I went yesterday.","client_attempt_id":"review-repeated-prompt-001"},headers=headers)
    assert repeated.status_code==200 and repeated.json()["attempt"]["assistance_level"]=="independent_production" and repeated.json()["item"]["stage"]==1
    with application.state.db.connect() as connection: assert connection.execute("SELECT COUNT(*) FROM review_hint_events WHERE review_item_id=? AND hint_level='H3'",(item["id"],)).fetchone()[0]>=2


def test_repeated_curated_transfer_is_not_labeled_novel(tmp_path: Path) -> None:
    client,application,headers=client_for(tmp_path,ScriptedTutor()); transfer="Tell a friend what you did on your last day off."
    first=create_session(client,headers,"transfer-session-create-1")
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE turns SET final_text=?,task_variant='transfer' WHERE id=?",(transfer,first["question"]["turn_id"]))
    answer1=client.post(f"/api/sessions/{first['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"transfer-answer-first"},headers=headers)
    assert answer1.json()["evaluation"]["assistance_level"]=="novel_context_transfer"
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE review_items SET due_at='2000-01-01T00:00:00+00:00'")
    review_item=client.get("/api/reviews/due").json()["items"][0]
    reviewed=client.post(f"/api/reviews/{review_item['id']}/attempts",json={"text":"I stayed home.","client_attempt_id":"transfer-cross-review"},headers=headers)
    assert reviewed.json()["attempt"]["assistance_level"]=="independent_production"
    client.post(f"/api/sessions/{first['id']}/end",json={"client_attempt_id":"transfer-end-first"},headers=headers)
    second=create_session(client,headers,"transfer-session-create-2")
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE turns SET final_text=?,task_variant='transfer' WHERE id=?",(transfer,second["question"]["turn_id"]))
    answer2=client.post(f"/api/sessions/{second['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"transfer-answer-second"},headers=headers)
    assert answer2.json()["evaluation"]["assistance_level"]=="independent_production"


def test_prior_review_prompt_makes_conversation_transfer_non_novel(tmp_path: Path) -> None:
    client,application,headers=client_for(tmp_path,ScriptedTutor()); first=create_session(client,headers,"review-first-create")
    client.post(f"/api/sessions/{first['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"review-first-source"},headers=headers)
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE review_items SET due_at='2000-01-01T00:00:00+00:00'")
    item=client.get("/api/reviews/due").json()["items"][0]; prompt=item["prompt_en"]
    reviewed=client.post(f"/api/reviews/{item['id']}/attempts",json={"text":"I stayed home.","client_attempt_id":"review-first-attempt"},headers=headers)
    assert reviewed.json()["attempt"]["assistance_level"]=="novel_context_transfer"
    client.post(f"/api/sessions/{first['id']}/end",json={"client_attempt_id":"review-first-end"},headers=headers)
    second=create_session(client,headers,"review-first-second-create")
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE turns SET final_text=?,intent_id=?,task_variant='transfer' WHERE id=?",(prompt,item["intent_id"],second["question"]["turn_id"]))
    answer=client.post(f"/api/sessions/{second['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"review-first-transfer-answer"},headers=headers)
    assert answer.json()["evaluation"]["assistance_level"]=="independent_production"


def test_export_preview_apply_and_delete_are_owner_scoped(tmp_path: Path) -> None:
    client,_,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers)
    client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"answer-attempt-0006"},headers=headers)
    exported=client.get("/api/export").json(); assert exported["schema_version"]=="1" and exported["data"]["turns"]
    preview=client.post("/api/import/preview",json=exported,headers=headers); assert preview.status_code==200
    applied=client.post("/api/import/apply",json={"preview_id":preview.json()["preview_id"],"client_attempt_id":"import-apply-0001"},headers=headers); assert applied.status_code==200,applied.text; assert applied.json()["imported"]["sessions"]==1
    repeated=client.post("/api/import/apply",json={"preview_id":preview.json()["preview_id"],"client_attempt_id":"import-apply-0002"},headers=headers); assert repeated.status_code==200 and repeated.json()["duplicate"] is True
    malformed={**exported,"export_id":"malformed-export","data":{**exported["data"],"turns":["not-an-object"]}}
    invalid=client.post("/api/import/preview",json=malformed,headers=headers); assert invalid.status_code==422 and invalid.json()["detail"]["code"]=="invalid_import"
    malformed_stage={**exported,"export_id":"malformed-stage-export","data":{**exported["data"],"review_items":[{**exported["data"]["review_items"][0],"stage":"bad"}]}}
    invalid_stage=client.post("/api/import/preview",json=malformed_stage,headers=headers); assert invalid_stage.status_code==422 and invalid_stage.json()["detail"]["code"]=="invalid_import"
    with client.app.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE expressions SET user_edited=1 WHERE source_turn_id IN (SELECT id FROM turns WHERE session_id=?)",(session["id"],))
    missing_confirmation=client.delete(f"/api/sessions/{session['id']}",headers=headers); assert missing_confirmation.status_code==400
    deleted=client.delete(f"/api/sessions/{session['id']}",headers={**headers,"X-Confirm-Delete":"session"}); assert deleted.status_code==200
    with client.app.state.db.connect() as connection: assert connection.execute("SELECT COUNT(*) FROM expressions WHERE source_turn_id IS NULL AND user_edited=1").fetchone()[0]==0


def test_delete_can_explicitly_preserve_edited_expression_without_source_quote(tmp_path: Path) -> None:
    client,application,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers,"preserve-create-001")
    answer=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"preserve-answer-001"},headers=headers).json()
    with application.state.db.transaction(immediate=True) as connection: connection.execute("UPDATE expressions SET user_edited=1 WHERE source_turn_id=?",(answer["turn"]["id"],))
    deleted=client.delete(f"/api/sessions/{session['id']}?preserve_edited_expressions=true",headers={**headers,"X-Confirm-Delete":"session"}); assert deleted.status_code==200
    with application.state.db.connect() as connection:
        expression=connection.execute("SELECT source_turn_id,user_edited FROM expressions").fetchone(); assert expression["source_turn_id"] is None and expression["user_edited"]==1


def test_session_delete_removes_transcript_from_all_database_text(tmp_path: Path) -> None:
    sentinel="UNIQUE_DELETE_SENTINEL_7Q9Z"; client,application,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers,"delete-sentinel-create")
    client.post(f"/api/sessions/{session['id']}/turns",json={"text":sentinel,"client_attempt_id":"delete-sentinel-answer"},headers=headers)
    exported=client.get("/api/export").json(); assert sentinel in str(exported)
    assert client.post("/api/import/preview",json=exported,headers=headers).status_code==200
    deleted=client.delete(f"/api/sessions/{session['id']}",headers={**headers,"X-Confirm-Delete":"session"}); assert deleted.status_code==200
    with application.state.db.connect() as connection:
        for table_row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            table=table_row[0].replace('"','""')
            for column in connection.execute(f'PRAGMA table_info("{table}")').fetchall():
                if "TEXT" not in str(column[2]).upper(): continue
                name=str(column[1]).replace('"','""')
                assert connection.execute(f'SELECT 1 FROM "{table}" WHERE instr(COALESCE(CAST("{name}" AS TEXT),\'\'),?)>0 LIMIT 1',(sentinel,)).fetchone() is None,(table,name)


def test_pause_discards_late_generation_without_saving_answer(tmp_path: Path) -> None:
    gate=GateTutor(); application=create_app(settings=settings(tmp_path),tutor=gate); orchestrator=application.state.orchestrator
    session=orchestrator.create_session("local-user","weekend-story","direct-create-0001")
    async def exercise() -> None:
        pending=asyncio.create_task(orchestrator.submit_turn("local-user",session["id"],text="I stayed home.",client_attempt_id="late-answer-0001",intent_id=None,task_variant=None))
        await gate.started.wait(); orchestrator.set_status("local-user",session["id"],"paused","pause-action-0001"); await asyncio.wait_for(gate.cancelled.wait(),1)
        with pytest.raises(HTTPException) as caught: await pending
        assert caught.value.detail["code"]=="generation_cancelled"
    asyncio.run(exercise())
    with application.state.db.connect() as connection:
        assert connection.execute("SELECT status FROM sessions WHERE id=?",(session["id"],)).fetchone()[0]=="paused"
        assert connection.execute("SELECT COUNT(*) FROM turns WHERE session_id=? AND role='user'",(session["id"],)).fetchone()[0]==0


def test_skip_is_idempotent_audited_and_cancels_pending_generation(tmp_path: Path) -> None:
    gate=GateTutor(); application=create_app(settings=settings(tmp_path),tutor=gate); orchestrator=application.state.orchestrator
    session=orchestrator.create_session("local-user","weekend-story","direct-create-skip-001"); original=session["question"]["turn_id"]
    orchestrator.next_hint("local-user",original,"skip-hint-0001")
    async def exercise() -> None:
        pending=asyncio.create_task(orchestrator.submit_turn("local-user",session["id"],text="I stayed home.",client_attempt_id="skip-pending-answer-001",intent_id=None,task_variant=None))
        await gate.started.wait(); skipped=orchestrator.skip_question("local-user",session["id"],"skip-action-0001"); duplicate=orchestrator.skip_question("local-user",session["id"],"skip-action-0001")
        assert skipped==duplicate and skipped["question"]["turn_id"]!=original
        await asyncio.wait_for(gate.cancelled.wait(),1)
        with pytest.raises(HTTPException) as caught: await pending
        assert caught.value.detail["code"]=="generation_cancelled"
    asyncio.run(exercise())
    with application.state.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM hint_events WHERE turn_id=?",(original,)).fetchone()[0]==1
        assert connection.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]==0
        assert connection.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND event_type='question_skipped'",(session["id"],)).fetchone()[0]==1


def test_failed_revision_preserves_prior_state_and_can_be_retried(tmp_path: Path) -> None:
    tutor=ScriptedTutor(); application=create_app(settings=settings(tmp_path),tutor=tutor); orchestrator=application.state.orchestrator
    session=orchestrator.create_session("local-user","weekend-story","direct-create-0002")
    first=asyncio.run(orchestrator.submit_turn("local-user",session["id"],text="I stayed home.",client_attempt_id="direct-answer-0001",intent_id=None,task_variant=None))
    orchestrator.tutor=FailingTutor()
    with pytest.raises(Exception,match="offline"):
        asyncio.run(orchestrator.revise_turn("local-user",first["turn"]["id"],"I relaxed at home.","failed-revision-0001"))
    with application.state.db.connect() as connection:
        turn=connection.execute("SELECT final_text,transcript_version FROM turns WHERE id=?",(first["turn"]["id"],)).fetchone(); evaluation=connection.execute("SELECT invalidated_at FROM evaluations WHERE turn_id=?",(first["turn"]["id"],)).fetchone(); state=connection.execute("SELECT learning_state,current_question_turn_id FROM sessions WHERE id=?",(session["id"],)).fetchone()
        assert tuple(turn)==("I stayed home.",1); assert evaluation["invalidated_at"] is None; assert state["learning_state"]=="question"; assert state["current_question_turn_id"] is not None
    orchestrator.tutor=ScriptedTutor()
    revised=asyncio.run(orchestrator.revise_turn("local-user",first["turn"]["id"],"I relaxed at home.","successful-revision-0001"))
    assert revised["turn"]["transcript_version"]==2


def test_spoken_turn_uses_current_retry_parent_instead_of_new_answer(tmp_path: Path) -> None:
    tutor=ScriptedTutor([lambda c:ready(c,correction=True),lambda c:ready(c)])
    application=create_app(settings=settings(tmp_path),tutor=tutor); orchestrator=application.state.orchestrator
    session=orchestrator.create_session("local-user","weekend-story","direct-create-0003")
    first=asyncio.run(orchestrator.submit_spoken_turn("local-user",session["id"],"I go yesterday.","spoken-answer-0001"))
    retried=asyncio.run(orchestrator.submit_spoken_turn("local-user",session["id"],"I went yesterday.","spoken-retry-0001"))
    assert retried["turn"]["attempt_kind"]=="retry"; assert retried["turn"]["parent_turn_id"]==first["turn"]["id"]


def test_two_distinct_independent_questions_recommend_reduced_support(tmp_path: Path) -> None:
    application=create_app(settings=settings(tmp_path),tutor=ScriptedTutor()); orchestrator=application.state.orchestrator
    session=orchestrator.create_session("local-user","weekend-story","direct-create-0004")
    first=asyncio.run(orchestrator.submit_spoken_turn("local-user",session["id"],"I stayed home.","support-answer-0001")); assert first["support_progress"]["distinct_independent_successes"]==1
    second=asyncio.run(orchestrator.submit_spoken_turn("local-user",session["id"],"I watched a movie.","support-answer-0002")); assert second["support_progress"]=={"intent_id":"past_activity","current_level":"A","proposed_level":"B","distinct_independent_successes":2}
    accepted=orchestrator.set_support_level("local-user","past_activity","B","support-choice-0001"); assert accepted["current_level"]=="B" and accepted["distinct_independent_successes"]==0


def test_optional_alternative_reveal_is_audited_and_affects_same_intent_question(tmp_path: Path) -> None:
    def with_alternative(context: dict[str,Any]) -> TutorEvaluation:
        value=ready(context); value.optional_alternative=OptionalAlternative(text_en="I spent the weekend at home.",usage_ko="같은 뜻의 다른 표현"); return value
    client,application,headers=client_for(tmp_path,ScriptedTutor([with_alternative,lambda c:ready(c)])); session=create_session(client,headers,"alternative-create-001")
    first=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"alternative-answer-001"},headers=headers).json()
    assert first["session"]["question"]["translation_ko"]
    exposure=client.post(f"/api/turns/{first['turn']['id']}/alternative-exposure",json={"client_attempt_id":"alternative-open-001"},headers=headers); assert exposure.status_code==200 and exposure.json()["current_question_affected"] is True
    second=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I spent the weekend at home.","client_attempt_id":"alternative-answer-002"},headers=headers).json(); assert second["evaluation"]["assistance_level"]=="model_imitation"
    with application.state.db.connect() as connection: assert connection.execute("SELECT COUNT(*) FROM alternative_exposures").fetchone()[0]==1


def test_marking_transcript_suspect_voids_progress_and_allows_revision(tmp_path: Path) -> None:
    client,application,headers=client_for(tmp_path,ScriptedTutor()); session=create_session(client,headers,"suspect-create-001")
    first=client.post(f"/api/sessions/{session['id']}/turns",json={"text":"I stayed home.","client_attempt_id":"suspect-answer-001"},headers=headers).json()
    suspect=client.post(f"/api/turns/{first['turn']['id']}/transcript-suspect",json={"client_attempt_id":"suspect-action-001"},headers=headers); assert suspect.status_code==200; assert suspect.json()["evaluation"]["evaluation_status"]=="unassessable"; assert suspect.json()["session"]["learning_state"]=="feedback"
    with application.state.db.connect() as connection: assert connection.execute("SELECT COUNT(*) FROM expressions WHERE source_turn_id=?",(first["turn"]["id"],)).fetchone()[0]==0
    revised=client.patch(f"/api/turns/{first['turn']['id']}",json={"text":"I relaxed at home.","client_attempt_id":"suspect-revise-001"},headers=headers); assert revised.status_code==200 and revised.json()["turn"]["transcript_version"]==2
