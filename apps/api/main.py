from __future__ import annotations

import asyncio
import hmac
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, File, Form, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import ROOT, Settings
from .data_service import LearningDataService
from .db import Database
from .models import (
    ActionRequest,
    CreateSessionRequest,
    ImportApplyRequest,
    PairRequest,
    ReviseTurnRequest,
    ReviewAttemptRequest,
    SubmitTurnRequest,
    SupportPreferenceRequest,
)
from .orchestrator import LessonOrchestrator, ScenarioCatalog
from .providers import ProviderUnavailable, TranscriptionError, WhisperCppProvider, build_tutor
from .security import (
    AUTH_COOKIE,
    CSRF_COOKIE,
    api_error,
    consume_pairing_code,
    create_auth_token,
    require_user,
    validate_csrf,
    validate_origin,
    verify_auth_token,
)


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 404 and "." not in Path(path).name:
            return await super().get_response("index.html", scope)
        return response


def _apply_boundary_headers(response: Response, *, api: bool) -> Response:
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if api:
        response.headers["Cache-Control"] = "no-store"
    return response


def create_app(*, settings: Settings | None = None, tutor: Any | None = None, whisper_provider: Any | None = None) -> FastAPI:
    active_settings = settings or Settings.from_env()
    db = Database(active_settings.database_path)
    db.migrate()
    catalog = ScenarioCatalog()
    tutor_provider = tutor or build_tutor(active_settings)
    whisper = whisper_provider or WhisperCppProvider(active_settings)
    audio_semaphore = asyncio.Semaphore(1)
    orchestrator = LessonOrchestrator(db, tutor_provider, active_settings, catalog)
    data_service = LearningDataService(db)
    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        orchestrator.expire_stale_sessions()
        async def heartbeat_sweeper() -> None:
            while True:
                await asyncio.sleep(15)
                await asyncio.to_thread(orchestrator.expire_stale_sessions)
        sweeper=asyncio.create_task(heartbeat_sweeper())
        try: yield
        finally:
            sweeper.cancel()
            try: await sweeper
            except asyncio.CancelledError: pass
    application = FastAPI(title="Language Conversation", version="1.0.0", docs_url=None, redoc_url=None,lifespan=lifespan)
    application.state.settings = active_settings
    application.state.db = db
    application.state.tutor = tutor_provider
    application.state.whisper = whisper
    application.state.orchestrator = orchestrator

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422,content={"detail":{"code":"validation_error","message":"The request body or parameters are invalid.","retryable":False,"errors":exc.errors()}})

    @application.exception_handler(ProviderUnavailable)
    async def provider_unavailable(_request: Request, exc: ProviderUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503,content={"detail":{"code":"provider_unavailable","message":str(exc),"retryable":True}})

    @application.exception_handler(TranscriptionError)
    async def transcription_error(_request: Request, exc: TranscriptionError) -> JSONResponse:
        return JSONResponse(status_code=422,content={"detail":{"code":str(exc),"message":"The audio could not be transcribed safely.","retryable":True}})

    @application.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(_request: Request, _exc: sqlite3.IntegrityError) -> JSONResponse:
        return JSONResponse(status_code=409,content={"detail":{"code":"data_conflict","message":"The operation conflicts with existing learning data.","retryable":False}})

    @application.middleware("http")
    async def security_boundary(request: Request, call_next: Any) -> Response:
        is_api = request.url.path.startswith("/api/")
        if is_api and active_settings.edge_secret:
            supplied_secret = request.headers.get("x-lc-edge-secret", "")
            if not hmac.compare_digest(
                supplied_secret.encode("utf-8"),
                active_settings.edge_secret.encode("utf-8"),
            ):
                return _apply_boundary_headers(
                    JSONResponse(
                        status_code=403,
                        content={
                            "detail": {
                                "code": "edge_access_rejected",
                                "message": "The API request did not pass the configured edge boundary.",
                                "retryable": False,
                            }
                        },
                    ),
                    api=True,
                )
        if is_api and request.method not in {"GET","HEAD","OPTIONS"}:
            try:
                validate_origin(request,active_settings)
                if request.url.path != "/api/auth/pair": validate_csrf(request)
            except StarletteHTTPException as exc:
                return _apply_boundary_headers(
                    JSONResponse(status_code=exc.status_code,content={"detail":exc.detail}),
                    api=True,
                )
        if is_api: await asyncio.to_thread(orchestrator.expire_stale_sessions)
        response = await call_next(request)
        return _apply_boundary_headers(response, api=is_api)

    @application.get("/api/health")
    async def health() -> dict[str,Any]:
        return {"status":"ok","mode":active_settings.ai_provider}

    @application.get("/api/capabilities")
    async def capabilities(request: Request) -> dict[str,Any]:
        tutor_available = await tutor_provider.available() if hasattr(tutor_provider,"available") else True
        speech_available = await whisper.available()
        token=request.cookies.get(AUTH_COOKIE)
        return {"mode":"mock" if active_settings.ai_provider=="mock" else "real","tutor":{"available":tutor_available,"provider":active_settings.ai_provider,"model":tutor_provider.model_id},"speech_input":{"available":speech_available,"adapter":"whisper.cpp-http","max_seconds":active_settings.max_audio_seconds,"accepted_mime_types":sorted(whisper.allowed_mime_types)},"timezone":"Asia/Seoul","authenticated":verify_auth_token(token or "",active_settings) is not None,"external_paid_api":False}

    @application.post("/api/auth/pair")
    async def pair(payload: PairRequest,request: Request,response: Response) -> dict[str,Any]:
        if active_settings.edge_secret:
            fingerprint=request.headers.get("x-lc-client-ip") or "unknown"
        else:
            fingerprint=request.client.host if request.client else "unknown"
        user_id=consume_pairing_code(db,active_settings,payload.code,fingerprint)
        token,expires=create_auth_token(user_id,active_settings); csrf_token=__import__("secrets").token_urlsafe(32)
        response.set_cookie(AUTH_COOKIE,token,httponly=True,secure=active_settings.cookie_secure,samesite="strict",path="/",max_age=active_settings.session_ttl_seconds)
        response.set_cookie(CSRF_COOKIE,csrf_token,httponly=False,secure=active_settings.cookie_secure,samesite="strict",path="/",max_age=active_settings.session_ttl_seconds)
        return {"authenticated":True,"csrf_token":csrf_token,"expires_at":expires}

    @application.post("/api/auth/logout")
    async def logout(response: Response,_user_id: str=Depends(require_user)) -> dict[str,bool]:
        response.delete_cookie(AUTH_COOKIE,path="/",secure=active_settings.cookie_secure,samesite="strict")
        response.delete_cookie(CSRF_COOKIE,path="/",secure=active_settings.cookie_secure,samesite="strict")
        return {"ok":True}

    @application.get("/api/scenarios")
    async def scenarios(_user_id: str=Depends(require_user)) -> dict[str,Any]: return {"items":catalog.public_items()}

    @application.post("/api/sessions")
    async def create_session(payload: CreateSessionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.create_session(user_id,payload.scenario_id,payload.idempotency_key)

    @application.get("/api/sessions/{session_id}")
    async def get_session(session_id: str,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.get_session(user_id,session_id)

    @application.get("/api/sessions/{session_id}/events")
    async def events(session_id: str,after_sequence: int=0,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.events(user_id,session_id,max(after_sequence,0))

    @application.post("/api/sessions/{session_id}/turns")
    async def submit(session_id: str,payload: SubmitTurnRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return await orchestrator.submit_turn(user_id,session_id,text=payload.text,client_attempt_id=payload.client_attempt_id,intent_id=payload.intent_id,task_variant=payload.task_variant)

    @application.post("/api/sessions/{session_id}/audio")
    async def audio(session_id: str,file: UploadFile=File(...),client_attempt_id: str=Form(...),intent_id: str|None=Form(None),task_variant: str|None=Form(None),user_id: str=Depends(require_user)) -> dict[str,Any]:
        route_task=asyncio.current_task(); registered=False
        try:
            lease=orchestrator.session_audio_lease(user_id,session_id)
            if route_task is not None: orchestrator.register_input_task(session_id,route_task); registered=True
            if intent_id is not None or task_variant is not None: raise api_error(409,"audio_metadata_forbidden","Audio answers are linked to the current server question; omit intent_id and task_variant.")
            content_type=(file.content_type or "").split(";",1)[0].lower(); chunks=[]; total=0
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>active_settings.max_audio_bytes: raise api_error(413,"audio_too_large","Audio uploads are limited to 10 MiB.")
                chunks.append(chunk)
            raw_audio=b"".join(chunks); chunks.clear(); await file.close()
            try:
                async with audio_semaphore:
                    if orchestrator.session_audio_lease(user_id,session_id)!=lease: raise api_error(409,"stale_audio_capture","The session changed before transcription began. Record a new answer.")
                    text,latency_ms=await whisper.transcribe(raw_audio,content_type)
            finally:
                raw_audio=b""
            result=await orchestrator.submit_spoken_turn(user_id,session_id,text,client_attempt_id,lease)
            result["transcription"]={"text":text,"provider":"whisper.cpp","latency_ms":latency_ms,"source_mime_type":content_type}
            return result
        except asyncio.CancelledError:
            raise api_error(409,"input_cancelled","Audio processing was cancelled because the session lease or state changed.")
        finally:
            if registered and route_task is not None: orchestrator.unregister_input_task(session_id,route_task)
            await file.close()

    @application.patch("/api/turns/{turn_id}")
    async def revise(turn_id: str,payload: ReviseTurnRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return await orchestrator.revise_turn(user_id,turn_id,payload.text,payload.client_attempt_id)

    @application.post("/api/turns/{turn_id}/transcript-suspect")
    async def transcript_suspect(turn_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.mark_transcript_suspect(user_id,turn_id,payload.client_attempt_id)

    @application.post("/api/turns/{turn_id}/hints")
    async def hint(turn_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.next_hint(user_id,turn_id,payload.client_attempt_id)

    @application.post("/api/turns/{turn_id}/retry")
    async def retry(turn_id: str,payload: ReviseTurnRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return await orchestrator.retry(user_id,turn_id,payload.text,payload.client_attempt_id)

    @application.post("/api/turns/{turn_id}/continue")
    async def continue_turn(turn_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.continue_after_retry(user_id,turn_id,payload.client_attempt_id)

    @application.post("/api/turns/{turn_id}/alternative-exposure")
    async def alternative_exposure(turn_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.expose_alternative(user_id,turn_id,payload.client_attempt_id)

    @application.post("/api/sessions/{session_id}/pause")
    async def pause(session_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.set_status(user_id,session_id,"paused",payload.client_attempt_id)

    @application.post("/api/sessions/{session_id}/resume")
    async def resume(session_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.set_status(user_id,session_id,"active",payload.client_attempt_id)

    @application.post("/api/sessions/{session_id}/end")
    async def end(session_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.end_session(user_id,session_id,payload.client_attempt_id)

    @application.post("/api/sessions/{session_id}/skip")
    async def skip(session_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.skip_question(user_id,session_id,payload.client_attempt_id)

    @application.post("/api/sessions/{session_id}/heartbeat")
    async def heartbeat(session_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.heartbeat(user_id,session_id,payload.client_attempt_id)

    @application.get("/api/reviews/due")
    async def due(limit: int=3,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.due_reviews(user_id,max(1,min(limit,20)))

    @application.post("/api/reviews/{review_id}/hints")
    async def review_hint(review_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.review_hint(user_id,review_id,payload.client_attempt_id)

    @application.post("/api/reviews/{review_id}/attempts")
    async def review_attempt(review_id: str,payload: ReviewAttemptRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return await orchestrator.review_attempt(user_id,review_id,payload.model_dump())

    @application.post("/api/reviews/{review_id}/audio")
    async def review_audio(review_id: str,file: UploadFile=File(...),client_attempt_id: str=Form(...),user_id: str=Depends(require_user)) -> dict[str,Any]:
        try:
            lease=orchestrator.review_audio_lease(user_id,review_id)
            content_type=(file.content_type or "").split(";",1)[0].lower(); chunks=[]; total=0
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>active_settings.max_audio_bytes: raise api_error(413,"audio_too_large","Audio uploads are limited to 10 MiB.")
                chunks.append(chunk)
            raw_audio=b"".join(chunks); chunks.clear(); await file.close()
            try:
                async with audio_semaphore:
                    if orchestrator.review_audio_lease(user_id,review_id)!=lease: raise api_error(409,"stale_audio_capture","The review changed before transcription began. Record a new answer.")
                    text,latency_ms=await whisper.transcribe(raw_audio,content_type)
            finally:
                raw_audio=b""
            result=await orchestrator.review_attempt(user_id,review_id,{"text":text,"client_attempt_id":client_attempt_id,"task_variant":"review"},lease)
            result["transcription"]={"text":text,"provider":"whisper.cpp","latency_ms":latency_ms,"source_mime_type":content_type}; return result
        finally:
            await file.close()

    @application.post("/api/reviews/{review_id}/alternative-exposure")
    async def review_alternative_exposure(review_id: str,payload: ActionRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.expose_review_alternative(user_id,review_id,payload.client_attempt_id)

    @application.post("/api/intents/{intent_id}/support")
    async def set_support(intent_id: str,payload: SupportPreferenceRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return orchestrator.set_support_level(user_id,intent_id,payload.level,payload.client_attempt_id)

    @application.get("/api/history")
    async def history(user_id: str=Depends(require_user)) -> dict[str,Any]: return data_service.history(user_id)

    @application.get("/api/export")
    async def export(user_id: str=Depends(require_user)) -> JSONResponse:
        return JSONResponse(data_service.export(user_id),headers={"Content-Disposition":"attachment; filename=language-conversation-export.json","Cache-Control":"no-store"})

    @application.post("/api/import/preview")
    async def import_preview(payload: dict[str,Any]=Body(...),user_id: str=Depends(require_user)) -> dict[str,Any]: return data_service.preview_import(user_id,payload)

    @application.post("/api/import/apply")
    async def import_apply(payload: ImportApplyRequest,user_id: str=Depends(require_user)) -> dict[str,Any]: return data_service.apply_import(user_id,payload.preview_id)

    @application.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str,request: Request,preserve_edited_expressions: bool=False,user_id: str=Depends(require_user)) -> dict[str,Any]:
        if request.headers.get("x-confirm-delete") != "session": raise api_error(400,"confirmation_required","Set X-Confirm-Delete: session to delete this session.")
        return data_service.delete_session(user_id,session_id,preserve_edited_expressions)

    @application.delete("/api/profile/data")
    async def delete_all(request: Request,response: Response,user_id: str=Depends(require_user)) -> dict[str,Any]:
        if request.headers.get("x-confirm-delete") != "all-learning-data": raise api_error(400,"confirmation_required","Set X-Confirm-Delete: all-learning-data to delete all learning data.")
        result=data_service.delete_all(user_id); response.delete_cookie(AUTH_COOKIE,path="/"); response.delete_cookie(CSRF_COOKIE,path="/"); return result

    web_dist=ROOT/"apps"/"web"/"dist"
    if web_dist.exists(): application.mount("/",SPAStaticFiles(directory=web_dist,html=True),name="web")
    else:
        @application.get("/")
        async def root() -> dict[str,str]: return {"status":"api-ready","web":"build apps/web to serve the same-origin UI"}
    return application


app = create_app()
