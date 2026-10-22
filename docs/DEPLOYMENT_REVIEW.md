# 외부 테스트 배포 독립 검토

검토일: 2026-10-22. 구현 담당자와 별도 에이전트가 Pages Worker, FastAPI 경계, 실행 스크립트, 운영 문서와 관련 회귀 검사를 최종 공유 작업 트리에서 직접 검토했다. 대상은 Cloudflare Pages → Quick Tunnel → PC loopback API → loopback Ollama/whisper.cpp 경로다.

## 판정

**두 검토 축에서 미해결 BLOCKER 또는 IMPORTANT 코드 결함 없음.** 실제 공개 주소를 통한 왕복, 모바일 기기 동작, Cloudflare의 긴 원본 응답 제한은 이 독립 검토에서 검증하지 않았다. 이 검토만으로 전체 배포를 merge-ready 또는 모바일 음성 MVP 완료로 판정하지 않는다.

## Axis A — 동작·보안

- Worker는 `/api/`만 고정된 HTTPS `*.trycloudflare.com` 원본으로 전달하고, 원본 주소의 경로·자격 증명·쿼리·임의 도메인을 거부한다. 요청자가 보낸 전달 헤더와 edge secret을 제거한 뒤 서버 비밀을 주입한다. 상태 변경 요청의 Origin을 검사하고, 업스트림 리다이렉트를 따르지 않는다.
- API 응답과 경계 오류는 `Cache-Control: no-store`, 모든 응답은 `X-Robots-Tag: noindex, nofollow`다. Worker 응답이 여러 `Set-Cookie`를 유지하는 회귀 검사가 있다. FastAPI는 설정된 edge secret을 상수 시간 비교로 모든 `/api/` 경로에 요구하며, 외부 실행 스크립트는 서로 다른 48바이트 hex 인증·edge 비밀이 없으면 시작하지 않는다.
- 방문자별 페어링 시도 제한은 Cloudflare의 `CF-Connecting-IP`에서 Worker가 만든 헤더를 edge 인증 뒤에만 신뢰한다. 원본 IP는 데이터베이스에 저장하지 않고 인증 비밀을 키로 한 HMAC을 기록한다. 유효 기간이 지난 시도는 다음 시도 때 정리되며 전체 학습 데이터 삭제 때 남은 시도도 삭제된다. Cloudflare에서 방문자 IP 헤더를 제거하는 설정을 켜면 방문자가 `unknown`으로 묶여 제한을 공유할 수 있다.
- Worker의 360초 대기는 확인된 로컬 음성 턴(32.53–43.85초)보다 길다. 다만 Cloudflare의 기본 원본 응답 읽기 제한은 약 125초이므로, STT와 모델 재시도가 길어지는 경우 524가 날 수 있다. 장시간 경로의 성공은 실서버 통합 시험으로 확인해야 한다.

## Axis B — 코드베이스·운영 적합성

- 외부 테스트는 별도 SQLite 경로와 비밀을 사용하며, API·Ollama·whisper.cpp는 loopback에 바인딩된다. 정적 프런트엔드에는 edge secret이 들어가지 않는다. Quick Tunnel 주소 변경 후 Pages 원본 변수 갱신과 재배포가 필요한 절차를 운영 문서가 명시한다.
- Worker의 URL·헤더·쿠키·오류 처리와 FastAPI의 edge 경계는 기존 앱의 같은 출처 쿠키/CSRF 구조를 유지한다. 수동 Direct Upload와 PID 확인 후 중지 절차도 문서화됐다. 무료 플랜 설정, 실제 터널 지속성 및 브라우저 동작은 배포 환경에서 별도 확인이 필요하다.

## 독립 실행 증거와 남은 범위

- `node --test deploy/pages/tests/worker.test.mjs`: **7 passed**.
- `.venv/Scripts/python.exe -m pytest -q tests/test_edge_boundary.py tests/test_api_security.py --basetemp=.pytest-review-deployment-final`: **13 passed**, Starlette/httpx 사용 중단 경고 1개.
- `.venv/Scripts/python.exe -m compileall -q apps/api`: 성공.
- 기본 pytest 임시 디렉터리에는 이 검토 샌드박스의 접근 권한이 없어 프로젝트 안의 임시 디렉터리를 지정했다. 공개 Pages → Tunnel → API 왕복과 실제 휴대폰 마이크는 이 검토자가 실행하지 않았다.

외부 동작 근거: [Cloudflare Workers 제한](https://developers.cloudflare.com/workers/platform/limits/), [CF-Connecting-IP](https://developers.cloudflare.com/fundamentals/reference/http-headers/), [Cloudflare 524 제한](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/).
