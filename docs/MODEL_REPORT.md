# Language Conversation 모델·무료 실행 보고서

측정일 2026-10-22 Asia/Seoul. 실제 제공자 측정과 실제 모바일 결과를 분리한다.

## 환경과 선택

- Lenovo 20XKCTO1WW, AMD Ryzen5 PRO5650U 6코어/12스레드, RAM33,099,800,576bytes(약30.8GiB), AMD Radeon 통합 그래픽 보고값1GiB. Ollama ps에서 gemma3:4b의 100% CPU, context4096, llamacpp runner를 확인했다(evidence/ollama-ps.txt). STT는 선택한 CPU 배포본으로 실행했다.
- Windows, Node24.15.0/npm11.12.1, Python3.13.3, 기존 Ollama0.40.3. Ollama listen127.0.0.1:11434 확인. 기존 프로세스 설정을 변경하지 않음.
- 실행 후보 gemma3:4b / Q4_K_M /3,338,801,804bytes / digest `a2af6cc3eb7fa8be8504abaf9b04e88f17a119ec3f04a3addf55f92841195f5a`. 이미 설치되어 새 모델 다운로드 없음. [Gemma 약관](https://ai.google.dev/gemma/terms)은 모델 사용·제한 조건이며 Ollama 엔진 라이선스와 별개다. 앱과 가중치를 재배포하지 않음.
- 확인했으나 학습 기본값으로 선택하지 않은 모델 qwen2.5:3b /Q4_K_M /1.93GB. [공식 모델 라이선스](https://ollama.com/library/qwen2.5:3b)는 연구·평가 목적 Non-Commercial로 정의되므로 무료 일반 학습 사용권으로 해석하지 않음. 추후 Apache 모델 전환은 별도 실행·품질 검증 필요.
- STT whisper.cpp v1.7.6 Windows CPU 공식 x64 배포, 다국어 base142MiB(147,951,465bytes), [엔진](https://github.com/ggml-org/whisper.cpp/tree/v1.7.6) 및 [가중치](https://huggingface.co/ggerganov/whisper.cpp)의 MIT 표시 확인. 최신판이라고 주장하지 않으며 다운로드 SHA256은 evidence/download-hashes.json과 install_stt.ps1에 기록.
- TTS는 모바일 SpeechSynthesis 후보. Windows 합성 시험 Microsoft David와 실제 iPhone voice는 별개다. 음성 가중치 재배포 없음; 기기 음성 사용·외부 처리는 실제 기기에서 확인 필요.

## M0 실제 실행

[LLM 원자료](evidence/m0-llm.json): 최소 JSON 계약 직접 호출3턴. 첫 요청26,552.71ms(로드12,995ms), 웜11,370.53/8,317.62ms, 웜 중앙값9,844.075ms. 두 웜 요청만으로 p95를 산출하지 않음. 스키마 key 집합은 맞았으나 필드 간 일관성과 한국어 설명·후속 질문 품질 문제가 관찰됨. 최종 튜터 계약의 품질·성능과 동일시하지 않음.

[STT 원자료](evidence/m0-stt.json): Windows 음성 합성으로 만든 영어 한 문장 'I would like a cup of coffee, please.'를16kHz mono WAV로 변환. 3회 정확 전사, 요청 지연1,783.46/1,620.38/1,726.84ms. 실제 학습자의 발음·잡음·한국어 혼합·iPhone MIME 정확도는 미검증.

최종 스키마 실제 품질 시험은 scripts/evaluate_tutor.py 및 tests/fixtures/tutor_quality.json을 사용한다. 초기3개 결과는 evidence/tutor-quality.jsonl. Q01/Q02에서 상태/교정 모순으로 안전한 평가 보류 발생. 검증을 약화시키지 않고 상태 규칙·예시·복구 문맥을 보완한 뒤 별도 v2 파일에 재측정한다. AI가 작성한 기대 판정은 사람 검수 완료가 아니다. 자동 휴리스틱은 교정 제안의 적절성까지 검증하지 않는다.

## 비용·네트워크 경계

실행 코드의 제공자 경로는 HTTP loopback Ollama와 whisper.cpp뿐이며 유료 API 키/SDK/자동 결제/자동 유료 전환이 없다. 실제 M0 요청도 loopback에 보냈다. 패킷 캡처로 PC 전체 외부 트래픽 부재를 검증한 것은 아니다. 다운로드는 GitHub/HuggingFace, 의존성 설치는 패키지 저장소에 접속한다. 브라우저 음성의 localService 표시만으로 완전 오프라인을 보장하지 않는다.

Ollama cloud 기능을 별도로 끄는 [공식 방법](https://github.com/ollama/ollama/blob/main/docs/faq.mdx)은 OLLAMA_NO_CLOUD=1 후 재시작이다. 앱은 외부 제공자 URL과 :cloud 모델을 거부하며 설치 모델을 확인한다. 기존 Codex/ChatGPT 구독을 API 권한으로 사용하지 않는다.

## 판정

M0 기술 검증 부분 완료. 로컬 STT·LLM은 실제 실행했으나 음성 전체 왕복과 iPhone HTTPS/TTS는 미검증. 직접 LLM 시간만으로도 목표 중앙3초/p95 6초를 초과하므로 실시간 목표 달성 아님. 정확한30턴 전체 지연은 DEVICE_CHECKLIST.md에 따라 별도 수행해야 한다. 무료 대안은 더 짧은 구조화 출력·더 작은 로컬 모델·CPU 설정 비교이며 새 장비나 유료 전환을 자동 수행하지 않는다.


## 최종 고정50개 검수 — 제약 스키마 v3

`evidence/tutor-quality-v3.jsonl`과 `.meta.json`/`.summary.json`이 원자료다. 50개 전체 수행. 오류15개 모두 필수 교정 후보 반환, 정상15개 중14개 무교정/1개 불필요 필수 교정(Q20), 허용 구어체10개 모두 필수 교정 없음. 불확실/불완전10개에서 필수 교정0, 평가보류7. 자동 휴리스틱은49/50이며 수정의 적절성·교정 근거를 사람이 검토한 정확도가 아니다. 12/15 '적절한' 교정이라는 기준은 사람 검수 전까지 통과로 처리하지 않는다.

최종 제공자 호출 wall time 중앙값23,166.75ms, p95 26,560.3ms. STT/업로드/VAD/기기 TTS가 빠진 LLM 단독 지연으로, 모바일 전체 목표를 달성하지 못했다. 프롬프트/스키마/제공자 소스 해시를 저장했고 초기 실패 원자료도 보존했다. 앱 개발·테스트가 병행된 조건이므로 통제된 하드웨어 벤치마크라고 주장하지 않는다.

FFmpeg는 기존 설치8.1 full Gyan Windows 빌드이며 `--enable-gpl --enable-version3`가 설정된 별도 도구다(evidence/ffmpeg-version.txt). 이 프로젝트는 해당 바이너리를 배포하지 않는다. 모델·whisper.cpp 엔진·FFmpeg·기기 음성의 조건을 하나의 라이선스로 묶지 않는다.

## 최종 실제 API 음성 한 턴 재검사

새 패치 잠금 런타임과 최종 제약 스키마로 단독 재검사했다. 합성 영어 업로드 → FFmpeg → 실제 whisper.cpp → 실제 Ollama → 서버 검증에서 HTTP200/`evaluation_status=ready`/필수 교정 없음/서버 독립 답변 성공 기록을 확인했다. 전체 API 요청32,526.5ms, STT2,226ms. 원자료 `evidence/real-api-audio-final.json`. 초기 평가보류98.9초 기록은 비교 조건(동시 품질검사 등)이 달라 성능 향상률로 환산하지 않는다.

이 결과는 한 개 합성 문장의 서버 경로가 동작한다는 증거다. 실제 학습자의 음성·전 세션·iPhone SpeechSynthesis 재생·10턴 자동 왕복·전체 지연30턴 수용을 증명하지 않는다. 지연 목표는 여전히 미달이다.

최종 자동 언어 감지 설정의 서버 음성 재검사도 성공했다(`evidence/real-api-audio-final-auto.json`):43,849.66ms, STT3,235ms, ready/필수 교정 없음. 의존성 설치·개발 검사가 병행된 조건이며, 이전 영어 고정 요청32.53초와 비교해 성능 개선율을 계산하지 않는다. 최종 실제 모바일 전체 지연은 여전히 미측정이고 서버 경로도 목표 초과다.
