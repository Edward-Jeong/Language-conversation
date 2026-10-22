from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from datetime import UTC, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import ROOT, Settings
from .db import Database, iso_now, json_load, row_dict, utc_now
from .models import TutorEvaluation
from .providers import ProviderUnavailable, TutorProvider
from .security import api_error


PROMPT_VERSION = "tutor_v1"
SCHEMA_VERSION = "1"
MAX_RETRIES = 2
REVIEW_INTERVALS = (1, 3, 7, 14)
REVIEW_MAX_RETRIES = 2
HEARTBEAT_TIMEOUT_SECONDS = 60


class ScenarioCatalog:
    def __init__(self, path: Path | None = None):
        payload = json.loads((path or ROOT / "content" / "scenarios" / "scenarios.v1.json").read_text(encoding="utf-8"))
        self.schema_version = payload["schema_version"]
        self._items = {item["id"]: item for item in payload["scenarios"]}
        self._translations = json.loads((ROOT / "content" / "scenarios" / "translations.ko.v1.json").read_text(encoding="utf-8"))
        if len(self._items) != 5:
            raise RuntimeError("scenario catalog must contain exactly five unique MVP scenarios")

    def get(self, scenario_id: str) -> dict[str, Any]:
        try:
            return self._items[scenario_id]
        except KeyError as exc:
            raise api_error(404, "scenario_not_found", "The requested scenario does not exist.") from exc

    def public_items(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item["id"],
                "version": item["version"],
                "title_ko": item["title_ko"],
                "description_ko": item["description_ko"],
                "intents": item["intents"],
            }
            for item in self._items.values()
        ]

    def translation(self,text_en: str) -> str:
        try: return self._translations[text_en]
        except KeyError as exc: raise RuntimeError(f"missing Korean translation for curated question: {text_en}") from exc


class LessonOrchestrator:
    def __init__(self, db: Database, tutor: TutorProvider, settings: Settings, scenarios: ScenarioCatalog):
        self.db = db
        self.tutor = tutor
        self.settings = settings
        self.scenarios = scenarios
        self._generation_tasks: dict[str,tuple[asyncio.AbstractEventLoop,asyncio.Task[TutorEvaluation]]] = {}
        self._input_tasks: dict[str,set[tuple[asyncio.AbstractEventLoop,asyncio.Task[Any]]]] = {}
        self._generation_lock = threading.Lock()

    def _register_generation(self,session_id: str,task: asyncio.Task[TutorEvaluation]) -> None:
        with self._generation_lock: self._generation_tasks[session_id]=(asyncio.get_running_loop(),task)

    def _unregister_generation(self,session_id: str,task: asyncio.Task[TutorEvaluation]) -> None:
        with self._generation_lock:
            current=self._generation_tasks.get(session_id)
            if current and current[1] is task: self._generation_tasks.pop(session_id,None)

    def _cancel_generation(self,session_id: str) -> None:
        with self._generation_lock: current=self._generation_tasks.get(session_id)
        if current:
            loop,task=current
            if not task.done(): loop.call_soon_threadsafe(task.cancel)

    def register_input_task(self,session_id: str,task: asyncio.Task[Any]) -> None:
        with self._generation_lock: self._input_tasks.setdefault(session_id,set()).add((asyncio.get_running_loop(),task))

    def unregister_input_task(self,session_id: str,task: asyncio.Task[Any]) -> None:
        with self._generation_lock:
            current=self._input_tasks.get(session_id,set()); current_copy={entry for entry in current if entry[1] is not task}
            if current_copy: self._input_tasks[session_id]=current_copy
            else: self._input_tasks.pop(session_id,None)

    def _cancel_input(self,session_id: str) -> None:
        with self._generation_lock: current=list(self._input_tasks.get(session_id,set()))
        for loop,task in current:
            if not task.done(): loop.call_soon_threadsafe(task.cancel)

    def _cancel_session_work(self,session_id: str) -> None:
        self._cancel_input(session_id); self._cancel_generation(session_id)

    @staticmethod
    def _request_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _next_sequence(connection: sqlite3.Connection, session_id: str) -> int:
        return int(connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE session_id=?", (session_id,)).fetchone()[0])

    def _event(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        event_type: str,
        revision: int,
        turn_id: str | None = None,
        attempt_id: str | None = None,
        transcript_version: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO events(event_id,session_id,turn_id,client_attempt_id,event_type,sequence,occurred_at,transcript_version,response_revision,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()), session_id, turn_id, attempt_id, event_type,
                self._next_sequence(connection, session_id), iso_now(), transcript_version, revision,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )

    def _question_dict(self,row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {"turn_id": row["id"], "text_en": row["final_text"], "translation_ko": self.scenarios.translation(row["final_text"]), "intent_id": row["intent_id"], "task_variant": row["task_variant"]}

    def _session_state(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        question = None
        if row["current_question_turn_id"]:
            question = connection.execute("SELECT * FROM turns WHERE id=?", (row["current_question_turn_id"],)).fetchone()
        question_data=self._question_dict(question)
        if question_data:
            preference=connection.execute("SELECT current_level FROM intent_support_preferences WHERE user_id=? AND intent_id=?",(row["user_id"],question_data["intent_id"])).fetchone()
            question_data["support_level"]=preference["current_level"] if preference else "A"
        current_turn=None; current_evaluation=None
        if row["current_retry_turn_id"]:
            retry_turn=connection.execute("SELECT * FROM turns WHERE id=?",(row["current_retry_turn_id"],)).fetchone()
            evaluation=connection.execute("SELECT * FROM evaluations WHERE turn_id=? AND invalidated_at IS NULL ORDER BY transcript_version DESC LIMIT 1",(row["current_retry_turn_id"],)).fetchone()
            if retry_turn: current_turn=self._turn_public(retry_turn)
            if evaluation:
                current_evaluation=json_load(evaluation["result_json"],{}); current_evaluation["next_question_en"]=None; current_evaluation["assistance_level"]=evaluation["assistance_level"]; current_evaluation["outcome"]=evaluation["outcome"]
        return {
            "id": row["id"],
            "scenario_id": row["scenario_id"],
            "status": row["status"],
            "learning_state": row["learning_state"],
            "media_state": row["media_state"],
            "question": question_data,
            "current_turn": current_turn,
            "current_evaluation": current_evaluation,
            "pending_next_question": bool(row["pending_next_question"]),
            "retry_count": row["retry_count"],
            "max_retries": MAX_RETRIES,
            "response_revision": row["response_revision"],
            "heartbeat_expires_at": (datetime.fromisoformat(row["heartbeat_at"])+timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)).isoformat() if row["heartbeat_at"] else None,
            "summary": json_load(row["summary_json"],None),
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _owned_session(connection: sqlite3.Connection, session_id: str, user_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if row is None:
            raise api_error(404, "session_not_found", "The session does not exist.")
        return row

    @staticmethod
    def _owned_turn(connection: sqlite3.Connection, turn_id: str, user_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT t.* FROM turns t JOIN sessions s ON s.id=t.session_id WHERE t.id=? AND s.user_id=?", (turn_id, user_id)
        ).fetchone()
        if row is None:
            raise api_error(404, "turn_not_found", "The turn does not exist.")
        return row

    def create_session(self, user_id: str, scenario_id: str, idempotency_key: str) -> dict[str, Any]:
        self.expire_stale_sessions()
        scenario = self.scenarios.get(scenario_id)
        payload = {"scenario_id": scenario_id}
        digest = self._request_hash(payload)
        scope = "create-session"
        with self.db.transaction(immediate=True) as connection:
            prior = connection.execute(
                "SELECT request_hash,response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",
                (user_id, scope, idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_hash"] != digest:
                    raise api_error(409, "idempotency_conflict", "This idempotency key was used for a different request.")
                return json.loads(prior["response_json"])
            if connection.execute("SELECT 1 FROM sessions WHERE user_id=? AND status='active'",(user_id,)).fetchone():
                raise api_error(409,"active_session_exists","Pause or end the active session before starting another one.")
            now = iso_now()
            session_id = str(uuid4())
            prior_count=connection.execute("SELECT COUNT(*) FROM sessions WHERE user_id=? AND scenario_id=?",(user_id,scenario_id)).fetchone()[0]
            question = scenario["opening_questions"][prior_count % len(scenario["opening_questions"])]
            question_id = str(uuid4())
            connection.execute(
                "INSERT INTO sessions(id,user_id,scenario_id,scenario_version,status,learning_state,media_state,current_question_turn_id,response_revision,started_at,updated_at,heartbeat_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id,user_id,scenario_id,scenario["version"],"active","question","idle",question_id,1,now,now,now),
            )
            connection.execute(
                "INSERT INTO turns(id,session_id,role,attempt_kind,final_text,intent_id,task_variant,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (question_id,session_id,"assistant","question",question["text_en"],question["intent_id"],"initial",now,now),
            )
            self._event(connection,session_id=session_id,event_type="question_ready",revision=1,turn_id=question_id,payload={"text_en":question["text_en"],"intent_id":question["intent_id"],"task_variant":"initial"})
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            result = self._session_state(connection, row)
            connection.execute(
                "INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",
                (user_id,scope,idempotency_key,digest,json.dumps(result,ensure_ascii=False),now),
            )
            return result

    def get_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        self.expire_stale_sessions()
        with self.db.connect() as connection:
            return self._session_state(connection, self._owned_session(connection, session_id, user_id))

    def expire_stale_sessions(self) -> list[str]:
        cutoff=(utc_now()-timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)).isoformat(); expired:list[str]=[]
        with self.db.transaction(immediate=True) as connection:
            rows=connection.execute("SELECT * FROM sessions WHERE status='active' AND (heartbeat_at IS NULL OR heartbeat_at<?)",(cutoff,)).fetchall()
            for session in rows:
                now=iso_now(); revision=int(session["response_revision"])+1
                connection.execute("UPDATE sessions SET status='paused',media_state='paused',response_revision=?,updated_at=? WHERE id=?",(revision,now,session["id"]))
                self._event(connection,session_id=session["id"],event_type="session_heartbeat_expired",revision=revision,payload={"timeout_seconds":HEARTBEAT_TIMEOUT_SECONDS}); expired.append(session["id"])
        for session_id in expired: self._cancel_session_work(session_id)
        return expired

    def heartbeat(self,user_id: str,session_id: str,client_attempt_id: str) -> dict[str,Any]:
        self.expire_stale_sessions(); scope=f"heartbeat:{session_id}"
        with self.db.transaction(immediate=True) as connection:
            session=self._owned_session(connection,session_id,user_id)
            if session["status"]!="active": raise api_error(409,"heartbeat_expired","The session lease expired or is paused. Resume explicitly before continuing.")
            now=iso_now(); expires=(utc_now()+timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)).isoformat(); connection.execute("UPDATE sessions SET heartbeat_at=?,updated_at=? WHERE id=?",(now,now,session_id))
            return {"ok":True,"expires_at":expires,"client_attempt_id":client_attempt_id}

    def session_audio_lease(self,user_id: str,session_id: str) -> dict[str,Any]:
        """Authorize speech capture before STT and snapshot the question it answers."""
        self.expire_stale_sessions()
        with self.db.connect() as connection:
            session=self._owned_session(connection,session_id,user_id)
            if session["status"]!="active": raise api_error(409,"session_not_active","Resume the paused session before recording an answer.")
            if session["media_state"]=="processing": raise api_error(409,"session_busy","Wait for the current answer to finish processing.",retryable=True)
            if session["learning_state"] not in {"question","retry"}: raise api_error(409,"answer_not_expected","The session is not waiting for an answer.")
            if not session["current_question_turn_id"]: raise api_error(409,"question_missing","The server has no current question for this answer.")
            if session["learning_state"]=="retry" and not session["current_retry_turn_id"]: raise api_error(409,"retry_turn_missing","The server has no current corrected answer to retry.")
            return {"response_revision":int(session["response_revision"]),"learning_state":session["learning_state"],"current_question_turn_id":session["current_question_turn_id"],"current_retry_turn_id":session["current_retry_turn_id"]}

    def _fallback_next(self, connection: sqlite3.Connection, session: sqlite3.Row) -> tuple[str, str, str]:
        scenario = self.scenarios.get(session["scenario_id"])
        count = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id=? AND role='assistant' AND attempt_kind='question'", (session["id"],)
        ).fetchone()[0]
        followups = scenario["follow_up_questions"]
        if count <= len(followups):
            item = followups[(count - 1) % len(followups)]
            return item["text_en"], item["intent_id"], "variation"
        transfers = scenario["transfer_tasks"]
        item = transfers[(count - len(followups) - 1) % len(transfers)]
        return item["text_en"], item["intent_id"], "transfer"

    def _context(self, connection: sqlite3.Connection, session: sqlite3.Row, turn: sqlite3.Row) -> dict[str, Any]:
        question = connection.execute("SELECT * FROM turns WHERE id=?", (session["current_question_turn_id"],)).fetchone()
        fallback, _, _ = self._fallback_next(connection, session)
        return {
            "turn_id": turn["id"],
            "transcript_version": turn["transcript_version"],
            "scenario_id": session["scenario_id"],
            "intent_id": turn["intent_id"],
            "task_variant": turn["task_variant"],
            "question_en": question["final_text"] if question else "",
            "learner_transcript": turn["final_text"],
            "fallback_next_question": fallback,
            "policy": {"max_corrections": 1, "max_alternatives": 1, "one_question": True},
        }

    @staticmethod
    def _transfer_seen(connection: sqlite3.Connection,user_id: str,prompt: str,intent_id: str | None,exclude_question_id: str | None=None) -> bool:
        conversation=connection.execute("SELECT 1 FROM turns q JOIN sessions s ON s.id=q.session_id WHERE s.user_id=? AND q.role='assistant' AND q.attempt_kind='question' AND q.task_variant='transfer' AND q.final_text=? AND (? IS NULL OR q.id<>?) LIMIT 1",(user_id,prompt,exclude_question_id,exclude_question_id)).fetchone()
        review=connection.execute("SELECT 1 FROM review_attempts ra JOIN review_items ri ON ri.id=ra.review_item_id JOIN expressions ex ON ex.id=ri.expression_id WHERE ra.user_id=? AND ex.intent_id=? AND ra.voided_at IS NULL LIMIT 1",(user_id,intent_id)).fetchone() if intent_id else None
        return bool(conversation or review)

    @classmethod
    def _assistance(cls,connection: sqlite3.Connection, question_turn_id: str | None, task_variant: str, evaluation: TutorEvaluation,user_id: str) -> tuple[str, str]:
        if evaluation.evaluation_status == "unassessable":
            return "unassessable", "unassessable"
        highest = connection.execute(
            "SELECT MAX(hint_level) FROM hint_events WHERE turn_id=?", (question_turn_id,)
        ).fetchone()[0] if question_turn_id else None
        if highest == "H3":
            assistance = "model_imitation"
        elif highest in {"H1", "H2"}:
            assistance = "supported_production"
        elif task_variant == "transfer" and question_turn_id:
            question=connection.execute("SELECT final_text,intent_id FROM turns WHERE id=?",(question_turn_id,)).fetchone()
            seen=bool(question and cls._transfer_seen(connection,user_id,question["final_text"],question["intent_id"],question_turn_id))
            assistance = "independent_production" if seen else "novel_context_transfer"
        else:
            assistance = "independent_production"
        return assistance, "needs_work" if evaluation.correction.needed or not evaluation.intent_achieved else "success"

    async def submit_turn(
        self,
        user_id: str,
        session_id: str,
        *,
        text: str,
        client_attempt_id: str,
        intent_id: str | None,
        task_variant: str | None,
        is_retry: bool = False,
        parent_turn_id: str | None = None,
        expected_audio_lease: dict[str,Any] | None = None,
    ) -> dict[str, Any]:
        self.expire_stale_sessions()
        scope = f"submit:{session_id}"
        payload = {"text": text, "intent_id": intent_id, "task_variant": task_variant, "is_retry": is_retry, "parent": parent_turn_id}
        digest = self._request_hash(payload)
        with self.db.transaction(immediate=True) as connection:
            session = self._owned_session(connection, session_id, user_id)
            prior = connection.execute(
                "SELECT request_hash,response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",
                (user_id,scope,client_attempt_id),
            ).fetchone()
            if prior:
                if prior["request_hash"] != digest:
                    raise api_error(409,"idempotency_conflict","This attempt ID was used for different content.")
                if prior["response_json"]:
                    return json.loads(prior["response_json"])
                raise api_error(409,"request_in_progress","This attempt is already being processed.",retryable=True)
            if expected_audio_lease is not None and any((
                int(session["response_revision"])!=expected_audio_lease["response_revision"],
                session["learning_state"]!=expected_audio_lease["learning_state"],
                session["current_question_turn_id"]!=expected_audio_lease["current_question_turn_id"],
                session["current_retry_turn_id"]!=expected_audio_lease["current_retry_turn_id"],
            )):
                raise api_error(409,"stale_audio_capture","The session changed while audio was transcribed. Record an answer for the current question.")
            if session["status"] != "active":
                raise api_error(409,"session_not_active","Resume the paused session before submitting an answer.")
            if session["media_state"]=="processing": raise api_error(409,"session_busy","Wait for the current answer to finish processing.",retryable=True)
            if session["learning_state"] == "retry" and not is_retry:
                raise api_error(409,"retry_required","Finish or continue past the current retry before answering another question.")
            if is_retry:
                if session["learning_state"] != "retry":
                    raise api_error(409,"retry_not_expected","This session is not waiting for a retry.")
                if session["retry_count"] >= MAX_RETRIES:
                    raise api_error(409,"retry_limit_reached","Use continue after the bounded retry limit.")
            question = connection.execute("SELECT * FROM turns WHERE id=?", (session["current_question_turn_id"],)).fetchone()
            if question is None:
                raise api_error(409,"question_missing","The server has no current question for this answer.")
            if intent_id is not None and intent_id != question["intent_id"]:
                raise api_error(409,"intent_mismatch","The answer intent must match the current server question.")
            if task_variant is not None and task_variant != question["task_variant"]:
                raise api_error(409,"task_variant_mismatch","The task variant must match the current server question.")
            effective_intent = question["intent_id"]
            server_variant = question["task_variant"]
            effective_variant = "retry" if is_retry else server_variant
            turn_id = str(uuid4())
            now = iso_now()
            revision = int(session["response_revision"]) + 1
            connection.execute(
                "INSERT INTO idempotency_keys(user_id,scope,key,request_hash,created_at) VALUES (?,?,?,?,?)",
                (user_id,scope,client_attempt_id,digest,now),
            )
            connection.execute(
                "INSERT INTO turns(id,session_id,role,attempt_kind,parent_turn_id,question_turn_id,final_text,intent_id,task_variant,client_attempt_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (turn_id,session_id,"user",effective_variant,parent_turn_id,question["id"],text,effective_intent,server_variant,client_attempt_id,now,now),
            )
            connection.execute(
                "UPDATE sessions SET media_state='processing',response_revision=?,updated_at=? WHERE id=?",
                (revision,now,session_id),
            )
            session = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            turn = connection.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
            context = self._context(connection, session, turn)
        started = time.perf_counter()
        generation_task=asyncio.create_task(self.tutor.evaluate(context))
        self._register_generation(session_id,generation_task)
        with self.db.connect() as connection:
            generation_session=self._owned_session(connection,session_id,user_id)
            if generation_session["status"]!="active" or int(generation_session["response_revision"])!=revision: generation_task.cancel()
        try:
            evaluation = await generation_task
            provider_status = "success" if evaluation.evaluation_status == "ready" else "invalid_output"
        except asyncio.CancelledError:
            with self.db.transaction(immediate=True) as connection:
                connection.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id))
                connection.execute("DELETE FROM turns WHERE id=?",(turn_id,))
                connection.execute("UPDATE sessions SET media_state='error',updated_at=? WHERE id=? AND response_revision=?",(iso_now(),session_id,revision))
            raise api_error(409,"generation_cancelled","Feedback generation was cancelled because the session changed.")
        except BaseException:
            with self.db.transaction(immediate=True) as connection:
                connection.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id))
                connection.execute("DELETE FROM turns WHERE id=?",(turn_id,))
                connection.execute("UPDATE sessions SET media_state='error',updated_at=? WHERE id=? AND response_revision=?",(iso_now(),session_id,revision))
            raise
        finally:
            self._unregister_generation(session_id,generation_task)
        latency_ms = int((time.perf_counter() - started) * 1000)
        with self.db.transaction(immediate=True) as connection:
            session = self._owned_session(connection, session_id, user_id)
            if session["status"] != "active" or session["response_revision"] != revision:
                connection.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id))
                connection.execute("DELETE FROM turns WHERE id=?",(turn_id,))
                connection.commit()
                raise api_error(409,"stale_generation_discarded","The session changed while feedback was generated; the late result was discarded.")
            assistance, outcome = self._assistance(connection, session["current_question_turn_id"], server_variant, evaluation,user_id)
            result_data = evaluation.model_dump(mode="json")
            connection.execute(
                "INSERT INTO evaluations(id,turn_id,transcript_version,response_revision,schema_version,model_version,prompt_version,result_json,assistance_level,outcome,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid4()),turn_id,1,revision,SCHEMA_VERSION,self.tutor.model_id,PROMPT_VERSION,json.dumps(result_data,ensure_ascii=False),assistance,outcome,evaluation.evaluation_status,iso_now()),
            )
            connection.execute(
                "INSERT INTO provider_runs(id,session_id,turn_id,provider_kind,model_id,latency_ms,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (str(uuid4()),session_id,turn_id,"tutor",self.tutor.model_id,latency_ms,provider_status,iso_now()),
            )
            fallback_text, fallback_intent, fallback_variant = self._fallback_next(connection, session)
            next_text = fallback_text
            correction_needed = evaluation.evaluation_status == "ready" and evaluation.correction.needed
            retry_count = int(session["retry_count"]) + (1 if is_retry else 0)
            if correction_needed:
                connection.execute(
                    "UPDATE sessions SET learning_state='retry',media_state='idle',pending_next_question=?,pending_next_intent_id=?,pending_next_task_variant=?,retry_count=?,current_retry_turn_id=?,updated_at=? WHERE id=?",
                    (next_text,fallback_intent,fallback_variant,retry_count,turn_id,iso_now(),session_id),
                )
                public_eval = dict(result_data)
                public_eval["next_question_en"] = None
            else:
                question_id = str(uuid4())
                now = iso_now()
                connection.execute(
                    "INSERT INTO turns(id,session_id,role,attempt_kind,final_text,intent_id,task_variant,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (question_id,session_id,"assistant","question",next_text,fallback_intent,fallback_variant,now,now),
                )
                connection.execute(
                    "UPDATE sessions SET learning_state='question',media_state='idle',current_question_turn_id=?,pending_next_question=NULL,pending_next_intent_id=NULL,pending_next_task_variant=NULL,retry_count=0,current_retry_turn_id=NULL,updated_at=? WHERE id=?",
                    (question_id,now,session_id),
                )
                self._event(connection,session_id=session_id,event_type="question_ready",revision=revision,turn_id=question_id,attempt_id=client_attempt_id,payload={"text_en":next_text,"intent_id":fallback_intent,"task_variant":fallback_variant})
                public_eval = result_data
            if outcome == "success" and assistance in {"independent_production","novel_context_transfer"}:
                self._ensure_review_item(connection,user_id,turn_id,effective_intent or "general",text)
            support_progress=self._support_progress(connection,user_id,effective_intent or "general",question["id"],assistance,outcome)
            self._event(connection,session_id=session_id,event_type="feedback_ready",revision=revision,turn_id=turn_id,attempt_id=client_attempt_id,transcript_version=1,payload={"evaluation_status":evaluation.evaluation_status,"assistance_level":assistance,"outcome":outcome,"next_question_withheld":correction_needed})
            session = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            turn = connection.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
            response = {
                "turn": self._turn_public(turn),
                "evaluation": {**public_eval,"assistance_level":assistance,"outcome":outcome},
                "session": self._session_state(connection, session),
                "support_progress": support_progress,
            }
            connection.execute(
                "UPDATE idempotency_keys SET response_json=? WHERE user_id=? AND scope=? AND key=?",
                (json.dumps(response,ensure_ascii=False),user_id,scope,client_attempt_id),
            )
            return response

    @staticmethod
    def _turn_public(turn: sqlite3.Row) -> dict[str, Any]:
        return {
            "id":turn["id"],"session_id":turn["session_id"],"role":turn["role"],"attempt_kind":turn["attempt_kind"],
            "parent_turn_id":turn["parent_turn_id"],"text":turn["final_text"],"transcript_version":turn["transcript_version"],
            "intent_id":turn["intent_id"],"task_variant":turn["task_variant"],"created_at":turn["created_at"],
        }

    def _ensure_review_item(self, connection: sqlite3.Connection, user_id: str, turn_id: str, intent_id: str, text: str) -> None:
        expression_id = str(uuid4())
        try:
            connection.execute(
                "INSERT INTO expressions(id,user_id,intent_id,canonical_form,acceptable_variants,source_turn_id,created_at) VALUES (?,?,?,?,?,?,?)",
                (expression_id,user_id,intent_id,text,"[]",turn_id,iso_now()),
            )
        except sqlite3.IntegrityError:
            row = connection.execute("SELECT id FROM expressions WHERE user_id=? AND intent_id=? AND canonical_form=?",(user_id,intent_id,text)).fetchone()
            expression_id = row["id"]
        if connection.execute("SELECT 1 FROM review_items WHERE expression_id=?",(expression_id,)).fetchone():
            return
        timezone_name = connection.execute("SELECT timezone FROM users WHERE id=?",(user_id,)).fetchone()[0]
        due = self._due_at(timezone_name, 1)
        connection.execute(
            "INSERT INTO review_items(id,expression_id,user_id,stage,due_at,created_at) VALUES (?,?,?,?,?,?)",
            (str(uuid4()),expression_id,user_id,0,due,iso_now()),
        )

    @staticmethod
    def _support_progress(connection: sqlite3.Connection,user_id: str,intent_id: str,question_turn_id: str,assistance: str,outcome: str) -> dict[str,Any]:
        connection.execute("INSERT OR IGNORE INTO intent_support_preferences(user_id,intent_id,current_level,updated_at) VALUES (?,?,'A',?)",(user_id,intent_id,iso_now()))
        current=connection.execute("SELECT current_level FROM intent_support_preferences WHERE user_id=? AND intent_id=?",(user_id,intent_id)).fetchone()[0]
        if outcome=="success" and assistance in {"independent_production","novel_context_transfer"}:
            connection.execute("INSERT OR IGNORE INTO intent_practice_successes(user_id,intent_id,question_turn_id,recorded_at) VALUES (?,?,?,?)",(user_id,intent_id,question_turn_id,iso_now()))
        elif outcome != "unassessable":
            connection.execute("DELETE FROM intent_practice_successes WHERE user_id=? AND intent_id=?",(user_id,intent_id))
            restored={"C":"B","B":"A","A":"A"}[current]
            if restored != current:
                connection.execute("UPDATE intent_support_preferences SET current_level=?,updated_at=? WHERE user_id=? AND intent_id=?",(restored,iso_now(),user_id,intent_id)); current=restored
        count=connection.execute("SELECT COUNT(*) FROM intent_practice_successes WHERE user_id=? AND intent_id=?",(user_id,intent_id)).fetchone()[0]
        proposed={"A":"B","B":"C","C":None}[current] if count>=2 else None
        return {"intent_id":intent_id,"current_level":current,"proposed_level":proposed,"distinct_independent_successes":count}

    def set_support_level(self,user_id: str,intent_id: str,level: str,client_attempt_id: str) -> dict[str,Any]:
        known=any(intent_id==intent["id"] for scenario in self.scenarios._items.values() for intent in scenario["intents"])
        if not known: raise api_error(404,"intent_not_found","The speaking intent does not exist.")
        scope=f"support:{intent_id}"; digest=self._request_hash({"level":level})
        with self.db.transaction(immediate=True) as connection:
            prior=connection.execute("SELECT request_hash,response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior:
                if prior["request_hash"]!=digest: raise api_error(409,"idempotency_conflict","This attempt ID was used for a different support level.")
                return json.loads(prior["response_json"])
            now=iso_now(); connection.execute("INSERT INTO intent_support_preferences(user_id,intent_id,current_level,updated_at) VALUES (?,?,?,?) ON CONFLICT(user_id,intent_id) DO UPDATE SET current_level=excluded.current_level,updated_at=excluded.updated_at",(user_id,intent_id,level,now)); connection.execute("DELETE FROM intent_practice_successes WHERE user_id=? AND intent_id=?",(user_id,intent_id)); result={"intent_id":intent_id,"current_level":level,"proposed_level":None,"distinct_independent_successes":0}; connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,scope,client_attempt_id,digest,json.dumps(result),now)); return result

    def expose_alternative(self,user_id: str,answer_turn_id: str,client_attempt_id: str) -> dict[str,Any]:
        scope=f"alternative:{answer_turn_id}"
        with self.db.transaction(immediate=True) as connection:
            turn=self._owned_turn(connection,answer_turn_id,user_id); session=self._owned_session(connection,turn["session_id"],user_id)
            evaluation=connection.execute("SELECT * FROM evaluations WHERE turn_id=? AND invalidated_at IS NULL ORDER BY transcript_version DESC LIMIT 1",(answer_turn_id,)).fetchone()
            if not evaluation: raise api_error(409,"alternative_unavailable","This answer has no current evaluation.")
            result=json_load(evaluation["result_json"],{}); alternative=result.get("optional_alternative")
            if not alternative or not alternative.get("text_en"): raise api_error(409,"alternative_unavailable","This answer has no optional alternative.")
            prior=connection.execute("SELECT response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior: return json.loads(prior["response_json"])
            current_question=connection.execute("SELECT * FROM turns WHERE id=?",(session["current_question_turn_id"],)).fetchone(); affected=bool(current_question and current_question["intent_id"]==turn["intent_id"])
            question_id=current_question["id"] if affected else None; now=iso_now()
            connection.execute("INSERT OR IGNORE INTO alternative_exposures(id,answer_turn_id,question_turn_id,content,exposed_at) VALUES (?,?,?,?,?)",(str(uuid4()),answer_turn_id,question_id,alternative["text_en"],now))
            if affected:
                connection.execute("INSERT OR IGNORE INTO hint_events(id,turn_id,hint_level,content_type,content,exposed_at) VALUES (?,?,?,?,?,?)",(str(uuid4()),question_id,"H3","optional_alternative",alternative["text_en"],now))
            revision=int(session["response_revision"])+1; connection.execute("UPDATE sessions SET response_revision=?,updated_at=? WHERE id=?",(revision,now,session["id"])); self._event(connection,session_id=session["id"],event_type="alternative_exposed",revision=revision,turn_id=answer_turn_id,attempt_id=client_attempt_id,payload={"current_question_affected":affected,"question_turn_id":question_id})
            response={"ok":True,"assistance_level":"model_imitation" if affected else "exposure_recorded","current_question_affected":affected}; connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,scope,client_attempt_id,self._request_hash({"answer_turn_id":answer_turn_id}),json.dumps(response),now)); return response

    @staticmethod
    def _due_at(timezone_name: str, days: int) -> str:
        zone = ZoneInfo(timezone_name)
        local_day = datetime.now(zone).date() + timedelta(days=days)
        return datetime.combine(local_day, datetime_time.min, tzinfo=zone).astimezone(UTC).isoformat()

    async def retry(self,user_id: str,turn_id: str,text: str,client_attempt_id: str) -> dict[str,Any]:
        with self.db.connect() as connection:
            original = self._owned_turn(connection,turn_id,user_id)
            session = self._owned_session(connection,original["session_id"],user_id)
            if session["learning_state"] != "retry" or session["current_retry_turn_id"] != turn_id:
                raise api_error(409,"stale_retry_turn","Retry the currently corrected answer, not an earlier turn.")
        return await self.submit_turn(user_id,original["session_id"],text=text,client_attempt_id=client_attempt_id,intent_id=None,task_variant=None,is_retry=True,parent_turn_id=turn_id)

    async def submit_spoken_turn(self,user_id: str,session_id: str,text: str,client_attempt_id: str,audio_lease: dict[str,Any] | None=None) -> dict[str,Any]:
        audio_lease=audio_lease or self.session_audio_lease(user_id,session_id)
        retry_turn_id=audio_lease["current_retry_turn_id"] if audio_lease["learning_state"]=="retry" else None
        return await self.submit_turn(user_id,session_id,text=text,client_attempt_id=client_attempt_id,intent_id=None,task_variant=None,is_retry=bool(retry_turn_id),parent_turn_id=retry_turn_id,expected_audio_lease=audio_lease)

    async def revise_turn(self,user_id: str,turn_id: str,text: str,client_attempt_id: str) -> dict[str,Any]:
        self.expire_stale_sessions()
        scope=f"revise:{turn_id}"; digest=self._request_hash({"text":text})
        with self.db.transaction(immediate=True) as connection:
            turn=self._owned_turn(connection,turn_id,user_id); session=self._owned_session(connection,turn["session_id"],user_id)
            if turn["role"] != "user": raise api_error(409,"turn_not_editable","Only learner transcripts can be revised.")
            if session["status"] != "active": raise api_error(409,"session_not_active","Resume the session before revising a transcript.")
            if session["media_state"]=="processing": raise api_error(409,"session_busy","Wait for the current operation to finish.",retryable=True)
            prior=connection.execute("SELECT request_hash,response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior:
                if prior["request_hash"] != digest: raise api_error(409,"idempotency_conflict","This attempt ID was used for different content.")
                if prior["response_json"]: return json.loads(prior["response_json"])
                raise api_error(409,"request_in_progress","This revision is already being processed.",retryable=True)
            latest=connection.execute("SELECT id FROM turns WHERE session_id=? AND role='user' ORDER BY created_at DESC,id DESC LIMIT 1",(session["id"],)).fetchone()
            if not latest or latest["id"] != turn_id: raise api_error(409,"revision_window_closed","Only the latest learner transcript can be revised.")
            if session["learning_state"] in {"retry","feedback"}:
                if session["current_retry_turn_id"] != turn_id: raise api_error(409,"stale_retry_turn","Only the currently corrected answer can be revised.")
                mode="retry"; next_question_id=None; pending=(session["pending_next_question"],session["pending_next_intent_id"],session["pending_next_task_variant"])
            elif session["learning_state"] == "question":
                next_question=connection.execute("SELECT * FROM turns WHERE id=?",(session["current_question_turn_id"],)).fetchone()
                if not next_question or connection.execute("SELECT 1 FROM hint_events WHERE turn_id=?",(next_question["id"],)).fetchone() or connection.execute("SELECT 1 FROM turns WHERE question_turn_id=? AND role='user'",(next_question["id"],)).fetchone():
                    raise api_error(409,"revision_window_closed","The next question has already been used; this transcript can no longer be revised safely.")
                mode="question"; next_question_id=next_question["id"]; pending=(next_question["final_text"],next_question["intent_id"],next_question["task_variant"])
            else:
                raise api_error(409,"revision_window_closed","This transcript cannot be revised in the current learning state.")
            version=int(turn["transcript_version"])+1; revision=int(session["response_revision"])+1; now=iso_now()
            connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,created_at) VALUES (?,?,?,?,?)",(user_id,scope,client_attempt_id,digest,now))
            connection.execute("UPDATE sessions SET media_state='processing',response_revision=?,updated_at=? WHERE id=?",(revision,now,session["id"]))
            question=connection.execute("SELECT * FROM turns WHERE id=?",(turn["question_turn_id"],)).fetchone(); fallback,_,_=self._fallback_next(connection,session)
            context={"turn_id":turn_id,"transcript_version":version,"scenario_id":session["scenario_id"],"intent_id":turn["intent_id"],"task_variant":turn["task_variant"],"question_en":question["final_text"] if question else "","learner_transcript":text,"fallback_next_question":pending[0] or fallback,"policy":{"max_corrections":1,"max_alternatives":1,"one_question":True}}
            session_id=session["id"]
        started=time.perf_counter()
        generation_task=asyncio.create_task(self.tutor.evaluate(context)); self._register_generation(session_id,generation_task)
        with self.db.connect() as connection:
            generation_session=self._owned_session(connection,session_id,user_id)
            if generation_session["status"]!="active" or int(generation_session["response_revision"])!=revision: generation_task.cancel()
        try: evaluation=await generation_task
        except asyncio.CancelledError:
            with self.db.transaction(immediate=True) as connection:
                connection.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)); connection.execute("UPDATE sessions SET media_state='error',updated_at=? WHERE id=? AND response_revision=?",(iso_now(),session_id,revision))
            raise api_error(409,"generation_cancelled","Transcript evaluation was cancelled because the session changed.")
        except BaseException:
            with self.db.transaction(immediate=True) as connection:
                connection.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)); connection.execute("UPDATE sessions SET media_state='error',updated_at=? WHERE id=? AND response_revision=?",(iso_now(),session_id,revision))
            raise
        finally: self._unregister_generation(session_id,generation_task)
        with self.db.transaction(immediate=True) as connection:
            session=self._owned_session(connection,session_id,user_id)
            if session["response_revision"] != revision or session["status"] != "active":
                connection.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)); connection.commit(); raise api_error(409,"stale_generation_discarded","The session changed while the revised transcript was evaluated.")
            if mode=="retry" and session["current_retry_turn_id"] != turn_id:
                connection.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)); connection.commit(); raise api_error(409,"stale_generation_discarded","The retry target changed while the transcript was evaluated.")
            if mode=="question":
                if session["current_question_turn_id"] != next_question_id or connection.execute("SELECT 1 FROM hint_events WHERE turn_id=?",(next_question_id,)).fetchone() or connection.execute("SELECT 1 FROM turns WHERE question_turn_id=? AND role='user'",(next_question_id,)).fetchone():
                    connection.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)); connection.commit(); raise api_error(409,"stale_generation_discarded","The next question was used while the transcript was evaluated.")
                connection.execute("DELETE FROM turns WHERE id=?",(next_question_id,))
            now=iso_now(); connection.execute("UPDATE evaluations SET invalidated_at=? WHERE turn_id=? AND invalidated_at IS NULL",(now,turn_id)); connection.execute("UPDATE review_attempts SET voided_at=? WHERE source_turn_id=? AND voided_at IS NULL",(now,turn_id)); connection.execute("DELETE FROM expressions WHERE source_turn_id=? AND user_edited=0",(turn_id,)); connection.execute("DELETE FROM intent_practice_successes WHERE user_id=? AND intent_id=? AND question_turn_id=?",(user_id,turn["intent_id"],turn["question_turn_id"])); connection.execute("UPDATE turns SET final_text=?,transcript_version=?,updated_at=? WHERE id=?",(text,version,now,turn_id)); turn=connection.execute("SELECT * FROM turns WHERE id=?",(turn_id,)).fetchone()
            assistance,outcome=self._assistance(connection,turn["question_turn_id"],turn["task_variant"],evaluation,user_id); data=evaluation.model_dump(mode="json")
            connection.execute("INSERT INTO evaluations(id,turn_id,transcript_version,response_revision,schema_version,model_version,prompt_version,result_json,assistance_level,outcome,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid4()),turn_id,version,revision,SCHEMA_VERSION,self.tutor.model_id,PROMPT_VERSION,json.dumps(data,ensure_ascii=False),assistance,outcome,evaluation.evaluation_status,iso_now()))
            connection.execute("INSERT INTO provider_runs(id,session_id,turn_id,provider_kind,model_id,latency_ms,status,created_at) VALUES (?,?,?,?,?,?,?,?)",(str(uuid4()),session["id"],turn_id,"tutor",self.tutor.model_id,int((time.perf_counter()-started)*1000),"success" if evaluation.evaluation_status=="ready" else "invalid_output",iso_now()))
            if outcome=="success" and assistance in {"independent_production","novel_context_transfer"}: self._ensure_review_item(connection,user_id,turn_id,turn["intent_id"] or "general",text)
            fallback_text,fallback_intent,fallback_variant=self._fallback_next(connection,session); next_text=pending[0] or fallback_text; next_intent=pending[1] or fallback_intent; next_variant=pending[2] or fallback_variant
            correction_needed=evaluation.evaluation_status=="ready" and evaluation.correction.needed
            if correction_needed:
                connection.execute("UPDATE sessions SET learning_state='retry',media_state='idle',pending_next_question=?,pending_next_intent_id=?,pending_next_task_variant=?,current_retry_turn_id=?,updated_at=? WHERE id=?",(next_text,next_intent,next_variant,turn_id,iso_now(),session["id"])); public_data=dict(data); public_data["next_question_en"]=None
            else:
                question_id=str(uuid4()); now=iso_now(); connection.execute("INSERT INTO turns(id,session_id,role,attempt_kind,final_text,intent_id,task_variant,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",(question_id,session["id"],"assistant","question",next_text,next_intent,next_variant,now,now)); connection.execute("UPDATE sessions SET learning_state='question',media_state='idle',current_question_turn_id=?,pending_next_question=NULL,pending_next_intent_id=NULL,pending_next_task_variant=NULL,current_retry_turn_id=NULL,retry_count=0,updated_at=? WHERE id=?",(question_id,now,session["id"])); self._event(connection,session_id=session["id"],event_type="question_ready",revision=revision,turn_id=question_id,attempt_id=client_attempt_id,payload={"text_en":next_text,"intent_id":next_intent,"task_variant":next_variant}); public_data=data
            self._event(connection,session_id=session["id"],event_type="transcript_revised",revision=revision,turn_id=turn_id,attempt_id=client_attempt_id,transcript_version=version,payload={"prior_evaluations_invalidated":True,"review_attempts_voided":True})
            turn=connection.execute("SELECT * FROM turns WHERE id=?",(turn_id,)).fetchone(); session=connection.execute("SELECT * FROM sessions WHERE id=?",(session["id"],)).fetchone()
            response={"turn":self._turn_public(turn),"evaluation":{**public_data,"assistance_level":assistance,"outcome":outcome},"session":self._session_state(connection,session)}
            connection.execute("UPDATE idempotency_keys SET response_json=? WHERE user_id=? AND scope=? AND key=?",(json.dumps(response,ensure_ascii=False),user_id,scope,client_attempt_id)); return response

    def mark_transcript_suspect(self,user_id: str,turn_id: str,client_attempt_id: str) -> dict[str,Any]:
        scope=f"suspect:{turn_id}"
        with self.db.transaction(immediate=True) as connection:
            turn=self._owned_turn(connection,turn_id,user_id); session=self._owned_session(connection,turn["session_id"],user_id)
            if turn["role"]!="user" or session["status"]!="active": raise api_error(409,"turn_not_editable","Only the latest active learner transcript can be marked uncertain.")
            latest=connection.execute("SELECT id FROM turns WHERE session_id=? AND role='user' ORDER BY created_at DESC,id DESC LIMIT 1",(session["id"],)).fetchone()
            if not latest or latest["id"]!=turn_id: raise api_error(409,"revision_window_closed","Only the latest learner transcript can be marked uncertain.")
            prior=connection.execute("SELECT response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior: return json.loads(prior["response_json"])
            if session["learning_state"] in {"retry","feedback"} and session["current_retry_turn_id"]==turn_id:
                pending=(session["pending_next_question"],session["pending_next_intent_id"],session["pending_next_task_variant"])
            elif session["learning_state"]=="question":
                next_question=connection.execute("SELECT * FROM turns WHERE id=?",(session["current_question_turn_id"],)).fetchone()
                if not next_question or connection.execute("SELECT 1 FROM hint_events WHERE turn_id=?",(next_question["id"],)).fetchone() or connection.execute("SELECT 1 FROM turns WHERE question_turn_id=? AND role='user'",(next_question["id"],)).fetchone(): raise api_error(409,"revision_window_closed","The next question has already been used.")
                pending=(next_question["final_text"],next_question["intent_id"],next_question["task_variant"]); connection.execute("DELETE FROM turns WHERE id=?",(next_question["id"],))
            else: raise api_error(409,"revision_window_closed","This transcript cannot be marked uncertain now.")
            evaluation=connection.execute("SELECT * FROM evaluations WHERE turn_id=? AND invalidated_at IS NULL ORDER BY transcript_version DESC LIMIT 1",(turn_id,)).fetchone()
            if not evaluation: raise api_error(409,"evaluation_missing","The transcript has no current evaluation.")
            result={"schema_version":"1","turn_id":turn_id,"transcript_version":turn["transcript_version"],"evaluation_status":"unassessable","intent_achieved":None,"meaning_summary_ko":None,"correction":{"needed":False,"original_span":None,"suggested_en":None,"reason_ko":None},"optional_alternative":None,"retry_prompt_en":None,"next_question_en":None,"uncertainty_reason":"사용자가 전사가 정확하지 않다고 표시했습니다."}; now=iso_now(); revision=int(session["response_revision"])+1
            connection.execute("UPDATE evaluations SET result_json=?,assistance_level='unassessable',outcome='unassessable',status='unassessable' WHERE id=?",(json.dumps(result,ensure_ascii=False),evaluation["id"])); connection.execute("UPDATE review_attempts SET voided_at=? WHERE source_turn_id=? AND voided_at IS NULL",(now,turn_id)); connection.execute("DELETE FROM expressions WHERE source_turn_id=? AND user_edited=0",(turn_id,)); connection.execute("DELETE FROM intent_practice_successes WHERE user_id=? AND intent_id=? AND question_turn_id=?",(user_id,turn["intent_id"],turn["question_turn_id"])); connection.execute("UPDATE sessions SET learning_state='feedback',media_state='idle',current_question_turn_id=?,current_retry_turn_id=?,pending_next_question=?,pending_next_intent_id=?,pending_next_task_variant=?,response_revision=?,updated_at=? WHERE id=?",(turn["question_turn_id"],turn_id,pending[0],pending[1],pending[2],revision,now,session["id"])); self._event(connection,session_id=session["id"],event_type="transcript_marked_uncertain",revision=revision,turn_id=turn_id,attempt_id=client_attempt_id,transcript_version=turn["transcript_version"])
            state=self._session_state(connection,connection.execute("SELECT * FROM sessions WHERE id=?",(session["id"],)).fetchone()); response={"turn":self._turn_public(turn),"evaluation":{**result,"assistance_level":"unassessable","outcome":"unassessable"},"session":state}; connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,scope,client_attempt_id,self._request_hash({"turn_id":turn_id}),json.dumps(response,ensure_ascii=False),now)); return response

    def continue_after_retry(self,user_id: str,turn_id: str,client_attempt_id: str) -> dict[str,Any]:
        with self.db.transaction(immediate=True) as connection:
            turn = self._owned_turn(connection,turn_id,user_id)
            session = self._owned_session(connection,turn["session_id"],user_id)
            if session["learning_state"] != "retry" or not session["pending_next_question"] or session["current_retry_turn_id"] != turn_id:
                raise api_error(409,"retry_not_expected","There is no pending retry to continue past.")
            existing = connection.execute("SELECT response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,f"continue:{session['id']}",client_attempt_id)).fetchone()
            if existing:
                return json.loads(existing["response_json"])
            question_id = str(uuid4()); now=iso_now(); revision=int(session["response_revision"])+1
            connection.execute("INSERT INTO turns(id,session_id,role,attempt_kind,final_text,intent_id,task_variant,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",(question_id,session["id"],"assistant","question",session["pending_next_question"],session["pending_next_intent_id"],session["pending_next_task_variant"],now,now))
            connection.execute("UPDATE sessions SET learning_state='question',media_state='idle',current_question_turn_id=?,pending_next_question=NULL,pending_next_intent_id=NULL,pending_next_task_variant=NULL,retry_count=0,current_retry_turn_id=NULL,response_revision=?,updated_at=? WHERE id=?",(question_id,revision,now,session["id"]))
            self._event(connection,session_id=session["id"],event_type="question_ready",revision=revision,turn_id=question_id,attempt_id=client_attempt_id,payload={"text_en":session["pending_next_question"],"intent_id":session["pending_next_intent_id"],"task_variant":session["pending_next_task_variant"]})
            state=self._session_state(connection,connection.execute("SELECT * FROM sessions WHERE id=?",(session["id"],)).fetchone())
            connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,f"continue:{session['id']}",client_attempt_id,self._request_hash({"turn_id":turn_id}),json.dumps(state,ensure_ascii=False),now))
            return state

    def skip_question(self,user_id: str,session_id: str,client_attempt_id: str) -> dict[str,Any]:
        scope=f"skip:{session_id}"
        with self.db.transaction(immediate=True) as connection:
            session=self._owned_session(connection,session_id,user_id)
            prior=connection.execute("SELECT response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior: return json.loads(prior["response_json"])
            if session["status"]!="active": raise api_error(409,"session_not_active","Resume the session before skipping a question.")
            if session["learning_state"]!="question": raise api_error(409,"skip_not_available","Use the retry continue action when feedback is waiting.")
            current=connection.execute("SELECT * FROM turns WHERE id=?",(session["current_question_turn_id"],)).fetchone()
            if not current: raise api_error(409,"question_missing","The server has no current question to skip.")
            next_text,next_intent,next_variant=self._fallback_next(connection,session); next_id=str(uuid4()); now=iso_now(); revision=int(session["response_revision"])+1
            connection.execute("INSERT INTO turns(id,session_id,role,attempt_kind,final_text,intent_id,task_variant,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",(next_id,session_id,"assistant","question",next_text,next_intent,next_variant,now,now))
            connection.execute("UPDATE sessions SET learning_state='question',media_state='idle',current_question_turn_id=?,pending_next_question=NULL,pending_next_intent_id=NULL,pending_next_task_variant=NULL,current_retry_turn_id=NULL,retry_count=0,response_revision=?,updated_at=? WHERE id=?",(next_id,revision,now,session_id))
            self._event(connection,session_id=session_id,event_type="question_skipped",revision=revision,turn_id=current["id"],attempt_id=client_attempt_id,payload={"outcome":"unassessable","next_question_turn_id":next_id})
            self._event(connection,session_id=session_id,event_type="question_ready",revision=revision,turn_id=next_id,attempt_id=client_attempt_id,payload={"text_en":next_text,"intent_id":next_intent,"task_variant":next_variant})
            result=self._session_state(connection,connection.execute("SELECT * FROM sessions WHERE id=?",(session_id,)).fetchone())
            connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,scope,client_attempt_id,self._request_hash({"question_turn_id":current["id"]}),json.dumps(result,ensure_ascii=False),now))
        self._cancel_session_work(session_id)
        return result

    def next_hint(self,user_id: str,turn_id: str,client_attempt_id: str) -> dict[str,Any]:
        with self.db.transaction(immediate=True) as connection:
            turn=self._owned_turn(connection,turn_id,user_id)
            session=self._owned_session(connection,turn["session_id"],user_id)
            scope=f"hint:{turn_id}"; prior_response=connection.execute("SELECT response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior_response: return json.loads(prior_response["response_json"])
            if turn["role"] != "assistant" or turn["attempt_kind"] != "question" or session["current_question_turn_id"] != turn_id:
                raise api_error(409,"hint_not_available","Hints are only available for the current question.")
            prior=connection.execute("SELECT MAX(hint_level) FROM hint_events WHERE turn_id=?",(turn_id,)).fetchone()[0]
            next_level={None:"H1","H1":"H2","H2":"H3"}.get(prior)
            if next_level is None:
                raise api_error(409,"hint_limit_reached","The full example has already been exposed.")
            scenario=self.scenarios.get(session["scenario_id"]); hint=scenario["hints"][turn["intent_id"]][next_level]
            content_type={"H1":"meaning_hint","H2":"english_stem","H3":"full_example"}[next_level]
            now=iso_now(); revision=int(session["response_revision"])+1
            connection.execute("INSERT INTO hint_events(id,turn_id,hint_level,content_type,content,exposed_at) VALUES (?,?,?,?,?,?)",(str(uuid4()),turn_id,next_level,content_type,hint,now))
            connection.execute("UPDATE sessions SET response_revision=?,updated_at=? WHERE id=?",(revision,now,session["id"]))
            self._event(connection,session_id=session["id"],event_type="hint_exposed",revision=revision,turn_id=turn_id,attempt_id=client_attempt_id,payload={"level":next_level,"content_type":content_type,"content":hint})
            state=self._session_state(connection,connection.execute("SELECT * FROM sessions WHERE id=?",(session["id"],)).fetchone())
            result={"hint":{"level":next_level,"content_type":content_type,"content":hint},"session":state}; connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,scope,client_attempt_id,self._request_hash({"turn_id":turn_id}),json.dumps(result,ensure_ascii=False),now)); return result

    def set_status(self,user_id: str,session_id: str,status: str,client_attempt_id: str) -> dict[str,Any]:
        self.expire_stale_sessions()
        with self.db.transaction(immediate=True) as connection:
            session=self._owned_session(connection,session_id,user_id)
            scope=f"status:{status}:{session_id}"; prior=connection.execute("SELECT response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior: return json.loads(prior["response_json"])
            if session["status"] == "ended":
                if status == "ended": return self._session_state(connection,session)
                raise api_error(409,"session_ended","An ended session cannot be resumed.")
            now=iso_now(); revision=int(session["response_revision"])+1
            learning="complete" if status=="ended" else session["learning_state"]
            media="ended" if status=="ended" else ("paused" if status=="paused" else "idle")
            connection.execute("UPDATE sessions SET status=?,learning_state=?,media_state=?,response_revision=?,updated_at=?,ended_at=CASE WHEN ?='ended' THEN ? ELSE ended_at END,heartbeat_at=CASE WHEN ?='active' THEN ? ELSE heartbeat_at END WHERE id=?",(status,learning,media,revision,now,status,now,status,now,session_id))
            self._event(connection,session_id=session_id,event_type=f"session_{status}",revision=revision,attempt_id=client_attempt_id)
            result=self._session_state(connection,connection.execute("SELECT * FROM sessions WHERE id=?",(session_id,)).fetchone()); connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,scope,client_attempt_id,self._request_hash({"status":status}),json.dumps(result,ensure_ascii=False),now))
        if status in {"paused","ended"}: self._cancel_session_work(session_id)
        return result

    def end_session(self,user_id: str,session_id: str,client_attempt_id: str) -> dict[str,Any]:
        state=self.set_status(user_id,session_id,"ended",client_attempt_id)
        with self.db.transaction(immediate=True) as connection:
            session=self._owned_session(connection,session_id,user_id)
            summary=json_load(session["summary_json"],None)
            if summary is None:
                corrections=connection.execute("SELECT COUNT(*) FROM evaluations e JOIN turns t ON t.id=e.turn_id WHERE t.session_id=? AND e.invalidated_at IS NULL AND json_extract(e.result_json,'$.correction.needed')=1",(session_id,)).fetchone()[0]
                initial_answers=connection.execute("SELECT COUNT(*) FROM turns WHERE session_id=? AND role='user' AND attempt_kind='initial'",(session_id,)).fetchone()[0]
                independent_successes=connection.execute("SELECT COUNT(*) FROM evaluations e JOIN turns t ON t.id=e.turn_id WHERE t.session_id=? AND e.invalidated_at IS NULL AND e.outcome='success' AND e.assistance_level IN ('independent_production','novel_context_transfer')",(session_id,)).fetchone()[0]
                corrected=connection.execute("SELECT json_extract(e.result_json,'$.correction.original_span'),json_extract(e.result_json,'$.correction.suggested_en') FROM evaluations e JOIN turns t ON t.id=e.turn_id WHERE t.session_id=? AND e.invalidated_at IS NULL AND json_extract(e.result_json,'$.correction.needed')=1 ORDER BY e.created_at LIMIT 3",(session_id,)).fetchall()
                expressions=connection.execute("SELECT canonical_form FROM expressions WHERE source_turn_id IN (SELECT id FROM turns WHERE session_id=?) ORDER BY created_at LIMIT 3",(session_id,)).fetchall()
                summary={"strength_ko":f"최초 답변 {initial_answers}개를 저장했고, 그중 {independent_successes}개를 도움 없이 전달했습니다.","initial_answer_count":initial_answers,"independent_success_count":independent_successes,"correction_count":corrections,"corrected_expressions":[{"original":r[0],"suggestion":r[1]} for r in corrected],"next_expressions":[r[0] for r in expressions]}
                connection.execute("UPDATE sessions SET summary_json=? WHERE id=?",(json.dumps(summary,ensure_ascii=False),session_id))
            state["summary"]=summary
            return {"session":state,"summary":summary}

    def events(self,user_id: str,session_id: str,after_sequence: int) -> dict[str,Any]:
        with self.db.connect() as connection:
            self._owned_session(connection,session_id,user_id)
            rows=connection.execute("SELECT * FROM events WHERE session_id=? AND sequence>? ORDER BY sequence LIMIT 100",(session_id,after_sequence)).fetchall()
            items=[]
            for row in rows:
                item=row_dict(row); item["payload"]=json_load(item.pop("payload_json"),{}); items.append(item)
            return {"events":items,"next_sequence":items[-1]["sequence"] if items else after_sequence}

    @staticmethod
    def _prepare_review_day(connection: sqlite3.Connection,item: sqlite3.Row) -> tuple[sqlite3.Row,str]:
        local_date=datetime.now(ZoneInfo(item["timezone"])).date().isoformat()
        if item["activity_learning_date"]!=local_date:
            connection.execute("UPDATE review_items SET activity_learning_date=?,attempt_count=0,current_hint_level='H0' WHERE id=?",(local_date,item["id"]))
            item=connection.execute("SELECT r.*,u.timezone,e.intent_id,e.canonical_form FROM review_items r JOIN users u ON u.id=r.user_id JOIN expressions e ON e.id=r.expression_id WHERE r.id=?",(item["id"],)).fetchone()
        return item,local_date

    @staticmethod
    def _review_item_public(item: sqlite3.Row) -> dict[str,Any]:
        result=row_dict(item); result["retry_count"]=max(0,int(item["attempt_count"])-1); result["max_retries"]=REVIEW_MAX_RETRIES
        result.pop("attempt_count",None); result.pop("activity_learning_date",None); result.pop("timezone",None); result.pop("user_id",None)
        return result

    def due_reviews(self,user_id: str,limit: int) -> dict[str,Any]:
        with self.db.transaction(immediate=True) as connection:
            rows=connection.execute("SELECT r.*,u.timezone,e.intent_id,e.canonical_form,e.acceptable_variants FROM review_items r JOIN users u ON u.id=r.user_id JOIN expressions e ON e.id=r.expression_id WHERE r.user_id=? AND r.due_at<=? ORDER BY r.due_at LIMIT ?",(user_id,iso_now(),limit)).fetchall(); items=[]
            for row in rows:
                row,_=self._prepare_review_day(connection,row); items.append({"id":row["id"],"expression_id":row["expression_id"],"intent_id":row["intent_id"],"prompt_en":self._review_prompt(row["intent_id"]),"task_variant":"transfer","current_hint_level":row["current_hint_level"],"stage":row["stage"],"due_at":row["due_at"],"retry_count":max(0,int(row["attempt_count"])-1),"max_retries":REVIEW_MAX_RETRIES})
            return {"items":items}

    def _review_prompt(self,intent_id: str) -> str:
        for scenario in self.scenarios._items.values():
            for task in scenario["transfer_tasks"]:
                if task["intent_id"] == intent_id: return task["text_en"]
            for question in scenario["follow_up_questions"]:
                if question["intent_id"] == intent_id: return question["text_en"]
        return "Respond naturally in English for this new situation."

    def review_audio_lease(self,user_id: str,review_id: str) -> dict[str,Any]:
        """Authorize review speech capture before STT and snapshot its assistance state."""
        with self.db.transaction(immediate=True) as connection:
            item=connection.execute("SELECT r.*,u.timezone,e.intent_id,e.canonical_form FROM review_items r JOIN users u ON u.id=r.user_id JOIN expressions e ON e.id=r.expression_id WHERE r.id=? AND r.user_id=?",(review_id,user_id)).fetchone()
            if not item: raise api_error(404,"review_not_found","The review item does not exist.")
            item,_=self._prepare_review_day(connection,item)
            return {key:item[key] for key in ("expression_id","stage","due_at","current_hint_level","activity_learning_date","attempt_count")}

    def review_hint(self,user_id: str,review_id: str,client_attempt_id: str) -> dict[str,Any]:
        with self.db.transaction(immediate=True) as connection:
            item=connection.execute("SELECT r.*,u.timezone,e.canonical_form,e.intent_id FROM review_items r JOIN users u ON u.id=r.user_id JOIN expressions e ON e.id=r.expression_id WHERE r.id=? AND r.user_id=?",(review_id,user_id)).fetchone()
            if not item: raise api_error(404,"review_not_found","The review item does not exist.")
            item,_=self._prepare_review_day(connection,item)
            scope=f"review-hint:{review_id}"; prior=connection.execute("SELECT response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior: return json.loads(prior["response_json"])
            level={"H0":"H1","H1":"H2","H2":"H3"}.get(item["current_hint_level"])
            if level is None: raise api_error(409,"hint_limit_reached","The full example has already been exposed.")
            expression=item["canonical_form"]
            content={"H1":"전달하려는 뜻을 짧고 자연스럽게 말해 보세요.","H2":" ".join(expression.split()[:2])+" ...","H3":expression}[level]
            connection.execute("UPDATE review_items SET current_hint_level=? WHERE id=?",(level,review_id))
            connection.execute("INSERT INTO review_hint_events(id,review_item_id,hint_level,content,exposed_at) VALUES (?,?,?,?,?)",(str(uuid4()),review_id,level,content,iso_now()))
            result={"hint":{"level":level,"content_type":{"H1":"meaning_hint","H2":"english_stem","H3":"full_example"}[level],"content":content},"item":{"id":review_id,"prompt_en":self._review_prompt(item["intent_id"]),"current_hint_level":level,"stage":item["stage"],"due_at":item["due_at"],"retry_count":max(0,int(item["attempt_count"])-1),"max_retries":REVIEW_MAX_RETRIES}}; connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,scope,client_attempt_id,self._request_hash({"review_id":review_id}),json.dumps(result,ensure_ascii=False),iso_now())); return result

    async def review_attempt(self,user_id: str,review_id: str,payload: dict[str,Any],expected_audio_lease: dict[str,Any] | None=None) -> dict[str,Any]:
        server_variant="transfer"
        with self.db.transaction(immediate=True) as connection:
            item=connection.execute("SELECT r.*,u.timezone,e.intent_id,e.canonical_form FROM review_items r JOIN users u ON u.id=r.user_id JOIN expressions e ON e.id=r.expression_id WHERE r.id=? AND r.user_id=?",(review_id,user_id)).fetchone()
            if not item: raise api_error(404,"review_not_found","The review item does not exist.")
            item,local_date=self._prepare_review_day(connection,item)
            prior=connection.execute("SELECT * FROM review_attempts WHERE user_id=? AND client_attempt_id=?",(user_id,payload["client_attempt_id"])).fetchone()
            if prior:
                evaluation=json_load(prior["evaluation_json"],{})
                return {"attempt":row_dict(prior),"item":self._review_item_public(item),"evaluation":evaluation}
            if int(item["attempt_count"])>=REVIEW_MAX_RETRIES+1: raise api_error(409,"review_retry_limit_reached","This card has reached today's bounded retry limit.")
            if expected_audio_lease is not None and any(item[key]!=expected_audio_lease[key] for key in ("expression_id","stage","due_at","current_hint_level","activity_learning_date","attempt_count")):
                raise api_error(409,"stale_audio_capture","The review prompt or hint level changed while audio was transcribed. Record a new answer.")
            attempt_id=str(uuid4()); context={"turn_id":attempt_id,"transcript_version":1,"scenario_id":"spaced-review","intent_id":item["intent_id"],"task_variant":server_variant,"question_en":self._review_prompt(item["intent_id"]),"learner_transcript":payload["text"],"fallback_next_question":None,"policy":{"max_corrections":1,"max_alternatives":1,"one_question":False}}
        evaluation=await self.tutor.evaluate(context)
        with self.db.transaction(immediate=True) as connection:
            item=connection.execute("SELECT r.*,u.timezone,e.intent_id FROM review_items r JOIN users u ON u.id=r.user_id JOIN expressions e ON e.id=r.expression_id WHERE r.id=? AND r.user_id=?",(review_id,user_id)).fetchone()
            if not item: raise api_error(404,"review_not_found","The review item does not exist.")
            prior=connection.execute("SELECT * FROM review_attempts WHERE user_id=? AND client_attempt_id=?",(user_id,payload["client_attempt_id"])).fetchone()
            if prior: return {"attempt":row_dict(prior),"item":self._review_item_public(item),"evaluation":json_load(prior["evaluation_json"],{})}
            if expected_audio_lease is not None and any(item[key]!=expected_audio_lease[key] for key in ("stage","due_at","current_hint_level","activity_learning_date","attempt_count")):
                raise api_error(409,"stale_audio_capture","The review state changed while feedback was generated; the late result was discarded.")
            if int(item["attempt_count"])>=REVIEW_MAX_RETRIES+1: raise api_error(409,"review_retry_limit_reached","This card has reached today's bounded retry limit.")
            hint_level=item["current_hint_level"]
            prior_prompt=self._transfer_seen(connection,user_id,self._review_prompt(item["intent_id"]),item["intent_id"])
            assistance={"H0":"independent_production" if prior_prompt else "novel_context_transfer","H1":"supported_production","H2":"supported_production","H3":"model_imitation"}[hint_level]
            outcome="unassessable" if evaluation.evaluation_status=="unassessable" else ("needs_work" if evaluation.correction.needed or not evaluation.intent_achieved else "success"); now=iso_now()
            eval_data={**evaluation.model_dump(mode="json"),"assistance_level":assistance,"outcome":outcome}
            connection.execute("INSERT INTO review_attempts(id,review_item_id,user_id,client_attempt_id,assistance_level,outcome,task_variant,attempted_at,transcript,evaluation_json) VALUES (?,?,?,?,?,?,?,?,?,?)",(attempt_id,review_id,user_id,payload["client_attempt_id"],assistance,outcome,server_variant,now,payload["text"],json.dumps(eval_data,ensure_ascii=False)))
            local_date=datetime.now(ZoneInfo(item["timezone"])).date().isoformat(); stage=int(item["stage"])
            if outcome=="success" and assistance in {"independent_production","novel_context_transfer"} and item["last_advanced_learning_date"] != local_date:
                stage=min(stage+1,len(REVIEW_INTERVALS)-1); due=self._due_at(item["timezone"],REVIEW_INTERVALS[stage]); last=local_date
            elif outcome=="unassessable":
                due=item["due_at"]; last=item["last_advanced_learning_date"]
            else:
                due=self._due_at(item["timezone"],1); last=item["last_advanced_learning_date"]
            next_hint=item["current_hint_level"]
            correction_example=evaluation.correction.suggested_en if evaluation.correction.needed else None
            if correction_example:
                next_hint="H3"; connection.execute("INSERT INTO review_hint_events(id,review_item_id,hint_level,content,exposed_at) VALUES (?,?,?,?,?)",(str(uuid4()),review_id,"H3",correction_example,now))
            connection.execute("UPDATE review_items SET stage=?,due_at=?,last_advanced_learning_date=?,current_hint_level=?,activity_learning_date=?,attempt_count=attempt_count+1 WHERE id=?",(stage,due,last,next_hint,local_date,review_id))
            updated=connection.execute("SELECT * FROM review_items WHERE id=?",(review_id,)).fetchone(); attempt=connection.execute("SELECT * FROM review_attempts WHERE id=?",(attempt_id,)).fetchone()
            return {"attempt":row_dict(attempt),"item":self._review_item_public(updated),"evaluation":eval_data}

    def expose_review_alternative(self,user_id: str,review_id: str,client_attempt_id: str) -> dict[str,Any]:
        scope=f"review-alternative:{review_id}"
        with self.db.transaction(immediate=True) as connection:
            item=connection.execute("SELECT r.*,u.timezone,e.intent_id,e.canonical_form FROM review_items r JOIN users u ON u.id=r.user_id JOIN expressions e ON e.id=r.expression_id WHERE r.id=? AND r.user_id=?",(review_id,user_id)).fetchone()
            if not item: raise api_error(404,"review_not_found","The review item does not exist.")
            item,_=self._prepare_review_day(connection,item)
            prior=connection.execute("SELECT response_json FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?",(user_id,scope,client_attempt_id)).fetchone()
            if prior: return json.loads(prior["response_json"])
            attempt=connection.execute("SELECT * FROM review_attempts WHERE review_item_id=? AND user_id=? AND voided_at IS NULL ORDER BY attempted_at DESC,id DESC LIMIT 1",(review_id,user_id)).fetchone()
            evaluation=json_load(attempt["evaluation_json"],{}) if attempt else {}
            alternative=evaluation.get("optional_alternative")
            if not isinstance(alternative,dict) or not alternative.get("text_en"): raise api_error(409,"alternative_unavailable","The latest review attempt has no optional alternative.")
            now=iso_now(); connection.execute("INSERT INTO review_hint_events(id,review_item_id,hint_level,content,exposed_at) VALUES (?,?,?,?,?)",(str(uuid4()),review_id,"H3",alternative["text_en"],now)); connection.execute("UPDATE review_items SET current_hint_level='H3' WHERE id=?",(review_id,)); updated=connection.execute("SELECT * FROM review_items WHERE id=?",(review_id,)).fetchone()
            response={"ok":True,"alternative":alternative,"assistance_level":"model_imitation","item":self._review_item_public(updated)}
            connection.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,response_json,created_at) VALUES (?,?,?,?,?,?)",(user_id,scope,client_attempt_id,self._request_hash({"review_id":review_id,"attempt_id":attempt["id"]}),json.dumps(response,ensure_ascii=False),now)); return response
