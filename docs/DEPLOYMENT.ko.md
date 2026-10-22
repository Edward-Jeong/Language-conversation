# Cloudflare Pages 외부 테스트

외부 주소: https://language-conversation.pages.dev

React 정적 파일 + Pages Function → 임시 Cloudflare Tunnel → 이 PC의 FastAPI/SQLite → loopback Ollama/whisper.cpp 구성이다. Pages에서 Python이나 모델을 실행하지 않는다. PC가 켜져 있고 API/STT/Tunnel 프로세스가 실행 중이어야 한다. 새 유료 플랜이나 유료 음성/LLM API는 연결하지 않았다. Cloudflare 무료 한도를 초과하면 오류로 중단하도록 fail-closed로 설정했다. 기존 계정 청구 상세는 연결된 OAuth에 Billing 권한이 없어 조회하지 못했다.

## 기기 연결

1. 이 PC에서 `pwsh -File scripts/start_external.ps1 -Pair`로 5분·1회용 페어링 코드를 발급한다.
2. 휴대폰에서 위 HTTPS 주소를 열고 코드를 입력한다.
3. 마이크/음성 권한은 실제 브라우저에서 허용한다. iPhone Safari 자동 음성·백그라운드 복귀는 아직 실기기 검증 전이다.

외부 테스트는 `data/external-test.sqlite3`와 별도 인증 비밀을 사용한다. 기존 개인 학습 DB를 공개하지 않는다. **현재 개인용 단일 사용자 구조이므로 코드로 연결한 모든 테스트 기기는 동일한 외부 테스트 학습 기록을 공유한다.** 신뢰할 수 있는 소수 기기로 테스트하고 민감한 자료는 입력하지 않는다. 일반 회원가입 서비스가 아니다.

## PC 재시작 후

프로젝트 루트의 PowerShell 터미널 세 개에서 각각 실행한다.

```powershell
pwsh -File scripts/start_stt.ps1
pwsh -File scripts/start_external.ps1
pwsh -File scripts/start_tunnel.ps1
```

기존 Ollama도 실행 중이어야 한다. `start_external.ps1`은 `.runtime/external-test.json`의 독립 인증/edge 비밀을 읽으며 로컬 HTTP만 127.0.0.1:8000에 바인딩한다. 쿠키는 브라우저의 Pages HTTPS 기준으로 Secure다. Ollama11434와 whisper8080을 외부에 노출하지 않는다. 새 설치에서는 아래 형식의 두 값을 각각 암호학적으로 무작위 48바이트 이상으로 생성해 `.runtime/external-test.json`에 저장한다. 이 파일은 커밋하지 않는다.

```json
{"auth_secret":"<random secret>","edge_secret":"<different random secret>"}
```

**Quick Tunnel 주소는 재시작 시 바뀐다.** 출력된 새 HTTPS 주소를 Pages 프로젝트 production 변수 `LC_BACKEND_ORIGIN`에 반영하고 재배포해야 한다. `LC_EDGE_SECRET`는 위 edge_secret과 같은 값의 암호화 secret binding이다. Pages·Tunnel 사이에서만 전송하며 프런트엔드 코드에 넣지 않는다. 고정 Tunnel에는 별도 도메인이 필요하며 현재 계정에 도메인이 없어 임시 경로를 선택했다. Tunnel에는 uptime 보장이 없다.

## 빌드·업로드

```powershell
npm ci --prefix apps/web
npm run build --prefix apps/web
node deploy/pages/prepare.mjs
npx wrangler pages deploy apps/web/dist --project-name language-conversation --branch codex/language-conversation-mvp
```

Wrangler는 사용자 Cloudflare 인증이 필요하다. 현재 배포는 연결된 Cloudflare API와 단기 Pages 업로드 JWT로 직접 업로드했다. `scripts/upload_pages_assets.py`는 단기 토큰 파일을 읽어 공개 빌드 자산만 업로드한 후 토큰 파일을 삭제한다. 이어서 동일 빌드의 manifest, `_worker.js`, `_routes.json`을 Pages deployments API로 제출한다. GitHub 설치 연결이 Cloudflare 측401 오류를 반환해 자동 Git 빌드는 설정하지 못했다. Git push만으로는 다음 배포가 갱신되지 않는다.

## 중지·폐기

직접 실행한 터미널은 Ctrl+C로 종료한다. 이번 백그라운드 실행 PID는 `.runtime/external-{api,stt,tunnel}.pid`에 기록된다. 해당 PID와 실행 파일을 확인한 뒤 해당 프로세스만 중지한다. 다른 Ollama 프로세스는 중지하지 않는다. 인증을 모두 폐기하려면 API를 중지한 뒤 외부 테스트 auth_secret을 교체하고 재시작한다. edge_secret 교체 시 Pages secret도 함께 갱신/재배포한다. 개인정보 삭제는 앱의 데이터 삭제 기능을 사용한다.

공식 근거: [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/), [Pages Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/), [Functions fail closed](https://developers.cloudflare.com/pages/functions/routing/).

## 지연 제약

Worker 자체 대기는 360초로 제한해 측정된 30~44초 로컬 음성 턴을 15초에 끊지 않는다. 다만 Cloudflare가 원본 응답을 기다리는 기본 제한 때문에 긴 원본 요청은 약125초에524 오류가 날 수 있다. STT와 모델 재시도의 최악 조합은 이를 초과할 수 있으며 장시간 안정성은 미검증이다. 이 경우 서버 상태를 다시 조회하고 결과를 확인한다. 지속 발생하면 비동기 작업·폴링으로 바꾸는 후속 구현이 필요하다. [공식524 설명](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/)
