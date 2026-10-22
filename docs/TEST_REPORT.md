# Language Conversation 검수 결과

검사일: 2026-10-22 Asia/Seoul. 최종 상태: **review-ready / 자동 음성 모바일 MVP 수용 전**. 구현, 자동 테스트, 실제 제공자, 실제 기기를 따로 판정한다. 공개 배포·원격 푸시·PR·병합은 하지 않았다.

## 단계 판정

| 단계 | 실행 코드·자동 검사 | 실제 제공자 | 실제 iPhone |
|---|---|---|---|
| M0 무료 실행 | 로컬 어댑터·설치/HTTPS/페어링·측정 스크립트 구현 | Ollama/whisper.cpp 실제 실행, 지연 목표 미달 | HTTPS/마이크/TTS 미검증 |
| M1 학습 루프 | 5개 시나리오·힌트·교정·재시도·지원 단계·전이 구현, 결정적 테스트 통과 | 최종 합성 음성 한 턴 전사·평가 성공, 전체 세션 미검증 | 실제 제공자 음성 한 세션 미검증 |
| M2 복습·보존 | 일정·전사 수정·멱등·가져오기·삭제·복구 구현 및 자동 검사 | 구조화 모델 결과를 기록, 사람 교정 검수 대기 | 다음날/1주 지연 실험 미수행 |
| M3 모바일 MVP | 반응형 UI·수명주기·오류 복구·설치 문서 구현 | 현재 PC 지연 목표 실패 | A01 등 필수 실기기 검사 미검증 |

UI/목업만 되는 상태를 자동 음성 완료라고 표시하지 않는다. 실제 모델 평가 보류를 HTTP200이라는 이유로 성공으로 바꾸지 않는다.

## 자동 검사

최종 집중 검사 숫자는 아래 '최종 재검사' 절과 독립 검토 문서를 따른다. 검사는 실제로 실행한 명령만 기록한다.

- 서버: `python -m pytest tests/test_learning_flow.py tests/test_api_security.py tests/test_product_boundaries.py -q --basetemp .runtime/pytest-final`. 교정→retry 중 다음 질문 차단, H3 따라 읽기/지원/독립 분리, 전사 버전/실패 복구, 서버 결정 intent/노출, 하루 한 번 진전, 서울 자정/연말, 가져오기/삭제/소유권, pairing Origin/CSRF/시도제한, 늦은 생성/전사 차단을 검사.
- 웹: `cd apps/web; npm test; npm run build`. MIME 선택, 재시도 오디오 계약, 노출·재개·수명주기 보조 로직을 검사. 이 단위 검사는 실제 마이크/스피커/자동10턴을 증명하지 않는다.
- 전체 저장소: `python -m pytest -q --basetemp .runtime/pytest-all` → 실행 당시 **36 passed / 5 failed / 25 subtests passed**. 이후 추가 앱 검사는 집중 명령으로 검사했다. 기존 `test_agent_installers.py`의 Bash3건은 WSL `/bin/bash` 부재, 같은 파일 symlink1건 및 `test_policy_updates.py` symlink1건은 WinError1314. 기존 테스트를 삭제·완화하거나 시스템 권한을 바꾸지 않았다. 전체 필수 검사 통과라고 보고하지 않는다.
- 실행 스크립트: PowerShell Parser로 start/pair/STT 설치·시작 파일 파싱 통과. Python 측정/인증서 스크립트 py_compile 통과.

## 브라우저·HTTPS

PC Chromium Playwright CLI, 명시적 목업/격리 DB. 360/390/430px에서 대화 화면 `scrollWidth == innerWidth`, 390px 홈 검사. 한글 줄바꿈·터치 영역 개선. 실제 가상 키보드·iPhone 안전 영역·물리 음성은 미검증.

페어링 → 시나리오 → 텍스트 정상 답변 → 다음 질문, 고정 오류 답변 → 핵심 교정 → 교정 중 후속 질문 DOM 부재를 확인했다. 재로드 뒤 현재 전사/교정이 복원됐다. 초기 복원 버튼 문제를 발견하여 서버 pause/resume 재조정과 진행 중 pause 완료 대기로 수정했다. 최신 상태는 단위 검사와 독립 검토에서 재확인한다.

화면 증거: `output/playwright/home-390-final.png`, `conversation-360.png`, `correction-gate-390.png`. 테스트 제공자 응답이므로 실제 모델 품질 증거가 아니다.

PC loopback HTTPS는 Python ssl CA 검증 활성 상태로 통과: 미인증401, Secure/HttpOnly/SameSite=strict cookie, 허용Origin 세션 생성, 다른Origin403, 1회용코드 재사용401. `docs/evidence/https-probe.json`. 인증서 확장필드 AKI/SKI 누락을 실제 검증에서 발견해 생성기를 고쳤다. 시스템 CA 신뢰/방화벽은 자동 설치하지 않았다.

## 실제 제공자·비용

- `scripts/m0_probe.py`: 기존 gemma3:4b 최소 JSON3턴. 콜드26.55초, 웜11.37/8.32초. 최종 앱 스키마 성능으로 대신하지 않는다.
- `scripts/m0_stt_probe.py`: Microsoft David 합성 영어 한 문장→whisper.cpp base 다국어3회 정확 전사,1.62~1.78초. 실제 학습자 발음/잡음 검수가 아니다.
- `scripts/check_real_audio.py`: API→FFmpeg→실제whisper.cpp→실제Ollama→검증 실행. 초기 스키마에서 HTTP200/평가보류,98.93초(STT4.003초), 동시 품질검사 실행 조건. 실패를 보존한 `docs/evidence/real-api-audio.json`.
- `scripts/evaluate_tutor.py`: 15오류/15정상/10구어체/10불확실의50개 고정 표본. 프롬프트 보완 v2와 제약 스키마 정합화 v3를 별도 원자료로 보존. 최종 해시/집계는 `docs/evidence/tutor-quality-v3.meta.json`, `.summary.json` 및 `.jsonl`을 따른다. 자동 교정 존재/오교정 집계는 사람이 수정의 적절성을 확인한 품질 검수와 다르다.
- 제공자 URL은 loopback에 제한하고 cloud 모델/유료 fallback은 거부한다. 모델/STT 요청은 로컬에 보냈다. PC 전체 네트워크 패킷 캡처나 기기TTS 외부 처리 검증은 수행하지 않았다. 모델·라이선스·CPU 사용·무료 대안은 MODEL_REPORT.md.

## 필수 수용 기준별 범위

| 기준 | 현재 증거 | 미수행 또는 한계 |
|---|---|---|
| A01 자동10턴 | 음성 제어 코드/단위검사 | 실제iPhone10턴 미검증 |
| A02 중지·복귀 | epoch/abort/revision/실제 요청 취소·늦은 STT 차단 검사 | 실기기 잠금/전화/echo 미검증 |
| A03 생각 멈춤 | 1.5초 기본·수동 답변 완료 | 학습자 실제 발화 조정 미검증 |
| A04 AI 음성 재인식0 | 출력 시 마이크 닫는 구현 | 스피커/이어폰0건 검수 미수행 |
| A05 교정 순서 | 서버 gate 테스트, 브라우저 후속질문DOM 부재 | 실제 음성 순서 미검증 |
| A06/A07 교정·다양한 정답 | 1교정 계약/구조 검증/실제50표본 원자료 | 사람 적절성 검수 대기 |
| A08 노출·독립 구분 | 힌트/대안/개선문 서버 노출 기록·분류 검사 | 기기 표시/발화 교차 검수 대기 |
| A09 전사 수정 | 버전/무효화/복원/보류 검사 | 실제 네트워크 재연결 검사 대기 |
| A10 일정 | UTC·Seoul 자정·hint·같은날 중복 검사 | 사용자 지연 복습 효과 미수행 |
| A11 전이 | 예문 없는 새 상황·권위있는 콘텐츠 질문 | 실제 학습자 전이 검수 대기 |
| A12 복구 | 공급자·권한 오류 UI 및 수명주기 검사 | 모든 iPhone 장애 조합 미검증 |
| A13 무료 경계 | 로컬 어댑터·명시목업·no paid fallback | TTS 완전오프라인 미검증 |
| A14 소유권 | pairing/expiry/rate/Origin/CSRF/소유권 검사 | LAN 기기 직접검사 대기 |
| A15 삭제 | 원문/자동카드/파생연결/명시보존 검사 | 비정상프로세스·기기캐시 실사 대기 |
| A16 레이아웃 | PC 360~430폭/스크린샷 검토 | iPhone 키보드/텍스트확대 미검증 |

## 독립 검토·재현

독립 Code Review는 두 축(동작·코드베이스 적합성), Design Review는 DESIGN.md와 화면 증거로 수행했다. 결과·잔여 제약은 `docs/INDEPENDENT_REVIEW.md`. 자동 검사와 외부 조건의 미검증 때문에 merge-ready/전체완료로 표시하지 않는다.

설치는 INSTALL.ko.md, 비밀값 없는 환경 예시는 `.env.example`, DB 스키마는 `migrations/`, 모델·기기·한계는 MODEL_REPORT/DEVICE_CHECKLIST/LIMITATIONS에 있다. 학습 효과를 확인하려면 사용자의 최초·다음날·약1주 실험이 별도로 필요하다.

## 최종 재검사

- 수정된 새 Python3.13 잠금 환경: 서버·제품 경계 집중 검사 **33 passed**. 독립 검토자가 동일 집중 검사를 실행했다.
- 최신 웹: **13 tests passed**, 프로덕션 빌드 성공. 독립 Code/Design Review 증거는 INDEPENDENT_REVIEW.md.
- 서버 의존성: 감사의 취약 기록33개(중복 포함)가 있는 초기 `pip-audit-before.json`을 보존했다. FastAPI0.158.1/Starlette1.9.0, cryptography50.0.0, python-multipart0.0.31로 수정·재잠금한 뒤 `pip-audit-after.json`에서 알려진 취약점0. 전역 Python 설치는 변경하지 않았다. 이 결과는 모든 보안 위험 부재를 보증하지 않는다.
- npm 감사: 공식 registry 감사 endpoint 시간 초과로 **미검증**(`npm-audit.json`). 빌드 성공을 취약점 검사 통과로 대체하지 않는다.
- 최종50개 실제 모델: **49/50 자동 휴리스틱**, 오류15개 필수교정 후보/정상·허용25개 중오교정1개/불확실10개 중보류7개. 사람 적절성 검수 대기. LLM 단독 중앙23.17초/p9526.56초로 지연 목표 실패.
- 새 잠금 환경의 CA 생성기와 실제 loopback TLS 서버를 다시 실행해 HTTPS/인증 검사를 통과했다. 사용자 PC/iPhone의 시스템 신뢰를 자동 변경하지 않았다.

최종 실제 제공자 재검사(`real-api-audio-final.json`): 패치 잠금 런타임의 합성 음성 한 턴이 **전사·평가 성공(ready)**,32.5265초(STT2.226초)를 기록했다. 초기 실패 원자료는 유지한다. 실제 제공자로 전체 세션을 완료했거나 자동 음성 MVP가 수용됐다는 뜻은 아니며, 지연 목표는 미달이다.


최종 runtime 설정(Whisper 자동 언어 감지·heartbeat 포함) 재검사: `real-api-audio-final-auto.json` → **전사 성공/ready/필수 교정 없음**, API43,849.66ms(STT3,235ms). 앱 의존성 설치와 개발 검사가 병행된 PC 조건이며 통제된 최대 성능 벤치마크는 아니다. 영어 전용 요청32.53초와 모두 보존했다. 모바일 VAD·업로드망·TTS를 제외해도 목표를 넘으므로 최종 지연 판정은 실패다.

최종 집중 검사 **33 서버/제품 테스트 + 13 웹 테스트**, 웹 빌드 성공. 동시 오디오 취소의 비결정적 테스트 준비 순서를 두 요청 등록 barrier로 고쳤으며, 동일 회귀5회와 독립 최종33개 검사가 통과했다. 남은 경고1개는 TestClient의 httpx→httpx2 전환 deprecation이며 실행 실패는 아니다.

실행 진입점도 실제 확인했다: 앱 전용 `.venv`로 `start.ps1 -LocalHttp -Port8002`가 real Ollama/STT available 상태로 기동하고 `pair.ps1`이 1회용 코드를 발급했다. 비밀값 없는 증거 `launcher-smoke.json`. 작업용 테스트 서버/브라우저는 종료했으며 시스템 신뢰·방화벽·기존 Ollama 설정은 바꾸지 않았다(`cleanup.json`).

## Cloudflare Pages 공개 경로 검증 (2026-10-22)

- 사용자 요청으로 Pages 외부 테스트 배포를 수행했다. 공개 HTTPS → Pages API 프록시 → Quick Tunnel → PC API → 실제 whisper.cpp/Ollama 연결이다. 별도 외부 테스트 DB를 사용한다.
- `scripts/check_pages_live.py`: 홈200/noindex, 미페어링401, 페어링200, Secure/SameSite/HttpOnly·다중 쿠키 유지, 잘못된Origin403, CSRF누락403, 세션생성/조회/종료200, 로그아웃후401, API no-store, Tunnel직접접근403 확인.
- 공개 경로 합성 음성 한 턴200, 45,337.24ms. 실제모델/전사 결과는 `docs/evidence/pages-live.json`. PC의 HTTP 클라이언트에서 수행했으며 iPhone 마이크·음성출력·모바일 전체 지연 검사가 아니다.
- 외부 Pages 화면을 PC Chromium 390×844에서 열어 캡처: `output/playwright/pages-pairing-390.png`.
- 독립 배포 검토: `docs/DEPLOYMENT_REVIEW.md`. 새 경계 Python검사13개·Worker검사7개 통과. 기존 웹13개·프로덕션빌드 통과. 전체저장소는63pass/5fail(기존WSL/bash·Windows symlink 권한). 이5개를 통과로 보고하지 않는다.
- 실제iPhone 자동음성,30턴지연,다음날/1주학습효과는 여전히 미검증이다. 긴원본요청524가능성과 PC상시실행 제약이 남아 있다.
