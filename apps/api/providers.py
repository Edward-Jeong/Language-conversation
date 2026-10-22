from __future__ import annotations

import asyncio
import copy
import json
import shutil
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from .config import ROOT, Settings
from .models import Correction, TutorEvaluation


class ProviderUnavailable(RuntimeError):
    pass


class TranscriptionError(RuntimeError):
    pass


class TutorProvider(Protocol):
    model_id: str

    async def evaluate(self, context: dict[str, Any]) -> TutorEvaluation: ...


class OllamaTutorProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model_id = settings.ollama_model
        self.prompt = (ROOT / "prompts" / "tutor_v1.txt").read_text(encoding="utf-8")
        self.schema = json.loads((ROOT / "schemas" / "tutor_response.schema.json").read_text(encoding="utf-8"))

    async def available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.5, trust_env=False, follow_redirects=False) as client:
                response = await client.get(f"{self.settings.ollama_url}/api/tags")
                response.raise_for_status()
                return any(model.get("name") == self.model_id for model in response.json().get("models", []))
        except (httpx.HTTPError, ValueError):
            return False

    async def evaluate(self, context: dict[str, Any]) -> TutorEvaluation:
        response_schema = copy.deepcopy(self.schema)
        response_schema["properties"]["turn_id"] = {"const": context["turn_id"]}
        response_schema["properties"]["transcript_version"] = {"const": context["transcript_version"]}
        request = {
            "model": self.model_id,
            "stream": False,
            "format": response_schema,
            "messages": [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "options": {"temperature": 0.2, "num_predict": 500},
        }
        last_error = "invalid structured output"
        for attempt in range(2):
            if attempt:
                request["messages"].append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous JSON was rejected. Error: " + last_error[:500]
                            + "\nReturn a corrected JSON object only. Preserve the input identity. "
                            + "If status is unassessable, correction.needed must be false and every correction detail must be null."
                        ),
                    }
                )
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=2.0), trust_env=False, follow_redirects=False) as client:
                    response = await client.post(f"{self.settings.ollama_url}/api/chat", json=request)
                    response.raise_for_status()
                raw = response.json()["message"]["content"]
                if len(raw.encode("utf-8"))>24_000: raise ValueError("model output exceeds 24 KB")
                parsed = TutorEvaluation.model_validate_json(raw)
                if parsed.turn_id != context["turn_id"] or parsed.transcript_version != context["transcript_version"]:
                    raise ValueError("model changed immutable turn identity")
                span = parsed.correction.original_span
                if parsed.correction.needed and span not in context["learner_transcript"]:
                    raise ValueError("correction span is not grounded in the transcript")
                return parsed
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise ProviderUnavailable(
                    f"Ollama is unavailable at the configured loopback URL ({self.settings.ollama_url})."
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise ProviderUnavailable(f"Ollama returned HTTP {exc.response.status_code}.") from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailable("Ollama request failed before a valid response was received.") from exc
            except (KeyError, ValueError, json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)
        return unassessable_evaluation(context, f"모델 출력 형식을 확인할 수 없습니다: {last_error[:120]}")


class MockTutorProvider:
    """Explicit UI/test provider. It intentionally makes no correctness judgment."""

    model_id = "explicit-mock-v1"

    async def available(self) -> bool:
        return True

    async def evaluate(self, context: dict[str, Any]) -> TutorEvaluation:
        return TutorEvaluation(
            schema_version="1",
            turn_id=context["turn_id"],
            transcript_version=context["transcript_version"],
            evaluation_status="ready",
            intent_achieved=True,
            meaning_summary_ko="목업 모드 응답입니다. 실제 교정 품질을 나타내지 않습니다.",
            correction=Correction(needed=False, original_span=None, suggested_en=None, reason_ko=None),
            optional_alternative=None,
            retry_prompt_en=None,
            next_question_en=context["fallback_next_question"],
            uncertainty_reason=None,
        )


def unassessable_evaluation(context: dict[str, Any], reason: str) -> TutorEvaluation:
    return TutorEvaluation(
        schema_version="1",
        turn_id=context["turn_id"],
        transcript_version=context["transcript_version"],
        evaluation_status="unassessable",
        intent_achieved=None,
        meaning_summary_ko=None,
        correction=Correction(needed=False, original_span=None, suggested_en=None, reason_ko=None),
        optional_alternative=None,
        retry_prompt_en=None,
        next_question_en=None,
        uncertainty_reason=reason,
    )


class WhisperCppProvider:
    allowed_mime_types = {
        "audio/webm": ".webm",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/ogg": ".ogg",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.temp_root=settings.database_path.parent / "audio-temp"
        self.temp_root.mkdir(parents=True,exist_ok=True)
        self._cleanup_stale_temp()

    def _cleanup_stale_temp(self) -> None:
        now=time.time()
        try: children=list(self.temp_root.iterdir())
        except OSError: return
        for child in children:
            try:
                if not self._expired_audio_temp(child.name,child.stat().st_mtime,now,self.settings.audio_temp_ttl_seconds): continue
                if child.is_symlink() or child.is_file(): child.unlink(missing_ok=True)
                elif child.is_dir(): shutil.rmtree(child)
            except OSError:
                continue

    @staticmethod
    def _expired_audio_temp(name: str,modified_at: float,now: float,ttl_seconds: int) -> bool:
        return name.startswith("lc-audio-") and now-modified_at>ttl_seconds

    async def available(self) -> bool:
        self._cleanup_stale_temp()
        try:
            base = self.settings.whisper_url.rsplit("/inference", 1)[0]
            async with httpx.AsyncClient(timeout=1.5, trust_env=False, follow_redirects=False) as client:
                response = await client.get(base)
                return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def transcribe(self, data: bytes, mime_type: str) -> tuple[str, int]:
        self._cleanup_stale_temp()
        if mime_type not in self.allowed_mime_types:
            raise TranscriptionError("unsupported_audio_format")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ProviderUnavailable("ffmpeg is not installed or not on PATH.")
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="lc-audio-",dir=self.temp_root) as directory:
            folder = Path(directory)
            source = folder / f"input{self.allowed_mime_types[mime_type]}"
            target = folder / "speech.wav"
            source.write_bytes(data)
            try:
                process = await asyncio.create_subprocess_exec(
                    ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(source), "-t", str(self.settings.max_audio_seconds + 1), "-ac", "1", "-ar", "16000", str(target),
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                )
                try: _stdout,stderr=await asyncio.wait_for(process.communicate(),timeout=55)
                except (TimeoutError,asyncio.CancelledError):
                    process.kill(); await process.wait(); raise
            except TimeoutError as exc:
                raise TranscriptionError("audio_conversion_failed") from exc
            except asyncio.CancelledError: raise
            except OSError as exc: raise TranscriptionError("audio_conversion_failed") from exc
            if process.returncode != 0 or not target.exists():
                raise TranscriptionError("audio_conversion_failed")
            with wave.open(str(target), "rb") as audio:
                duration = audio.getnframes() / max(audio.getframerate(), 1)
            if duration > self.settings.max_audio_seconds:
                raise TranscriptionError("audio_too_long")
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=2.0), trust_env=False, follow_redirects=False) as client:
                    with target.open("rb") as handle:
                        response = await client.post(
                            self.settings.whisper_url,
                            data={"response_format": "json", "language": "auto"},
                            files={"file": ("speech.wav", handle, "audio/wav")},
                        )
                    response.raise_for_status()
                payload = response.json()
                text = str(payload.get("text", "")).strip()
                if not text:
                    raise TranscriptionError("no_speech_detected")
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise ProviderUnavailable("whisper.cpp is unavailable at the configured loopback URL.") from exc
            except httpx.HTTPStatusError as exc:
                raise ProviderUnavailable(f"whisper.cpp returned HTTP {exc.response.status_code}.") from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailable("whisper.cpp request failed before a valid response was received.") from exc
            except (ValueError, KeyError) as exc:
                raise TranscriptionError("invalid_transcription_response") from exc
        return text, int((time.perf_counter() - start) * 1000)


def build_tutor(settings: Settings) -> TutorProvider:
    if settings.ai_provider == "mock":
        return MockTutorProvider()
    return OllamaTutorProvider(settings)
