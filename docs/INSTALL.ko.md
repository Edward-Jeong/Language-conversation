# Language Conversation 설치 및 실행

개인용 모바일 영어 회화 개발 버전입니다. 자동 음성 코드가 구현되어도 실제 iPhone 10턴 검수가 완료되었다는 뜻은 아닙니다. 현재 단계별 상태는 [TEST_REPORT.md](TEST_REPORT.md), 모델·지연은 [MODEL_REPORT.md](MODEL_REPORT.md)를 확인하세요. 공개 배포는 하지 않습니다.

## 1. PC 준비

Windows PowerShell, Python 3.13, Node.js 24, FFmpeg, 기존 Ollama가 필요합니다. 이 작업에서 시스템 설정이나 기존 Ollama 모델을 바꾸지 않았습니다. 프로젝트 루트에서 실행합니다.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock.txt
cd apps/web
npm ci
npm run build
cd ../..
Copy-Item .env.example .env
```

`.env`의 LC_ALLOWED_ORIGINS를 실제 PC LAN IP와 HTTPS 포트로 수정합니다. LC_MODEL 기본 후보는 설치된 `gemma3:4b`입니다. 미설치 상태에서는 자동 다운로드·유료 전환하지 않고 오류를 표시합니다. 모델 조건을 읽은 뒤 사용자가 `ollama pull gemma3:4b`로 받을 수 있습니다(약 3.3GB). 본 PC에는 이미 있었습니다.

Ollama는 loopback에 유지하세요. 서버 시작 환경의 `OLLAMA_NO_CLOUD=1`로 cloud 기능을 끌 수 있습니다. 기존 Ollama를 재시작해야 적용되므로 설치 스크립트는 사용자의 기존 프로세스나 설정을 강제로 바꾸지 않습니다. Ollama 11434/STT 8080 포트를 외부에 열지 않습니다.

```powershell
./scripts/install_stt.ps1
./scripts/start_stt.ps1
```

두 번째 명령은 STT 전용 터미널을 점유합니다. Ctrl+C로 중지합니다. 다운로드는 프로젝트 .runtime 안에 격리하며 체크섬이 다르면 실행하지 않습니다. 이 버전은 최신이라는 뜻이 아니라 실제 시험한 Windows 고정 배포본입니다. FFmpeg는 PATH에 있어야 하며 해당 배포본 라이선스는 별도 확인 대상입니다. 모델·엔진을 앱 소스와 함께 재배포하지 않습니다.

## 2. PC에서 먼저 실행

다른 터미널에서:

```powershell
./scripts/start.ps1 -LocalHttp
```

[PC 개발 화면](http://127.0.0.1:8000)에 접속합니다. 별도 터미널의 `./scripts/pair.ps1`이 출력한 1회용 코드를 화면에 입력합니다. 코드는 5분 유효하며 사용 후 재사용할 수 없습니다. Secret은 처음 실행에 data/auth-secret으로 생성됩니다. 로그아웃은 설정에서 합니다. HTTP 경로는 PC loopback 개발 전용입니다.

## 3. iPhone 같은 LAN HTTPS

유효하고 기기에서 신뢰되는 기존 인증서가 있으면 재사용하세요. 없다면 로컬 CA 파일만 생성합니다(자동 신뢰 설치 없음):

```powershell
.venv/Scripts/python scripts/create_local_cert.py --host 192.168.0.10
./scripts/start.ps1
```

IP는 예시입니다. 생성 전 `.cert`가 비어 있어야 하며 기존 키를 덮어쓰지 않습니다. 본인 PC/iPhone에 한해서 CA 신뢰를 수동 설정합니다. Windows는 ca.cer를 현재 사용자 신뢰 루트에 가져오기, iPhone은 ca.cer만 안전하게 전달하여 설정의 다운로드한 프로파일 설치 후 일반 → 정보 → 인증서 신뢰 설정에서 해당 개인 CA를 신뢰합니다. 개인키(`*-key.pem`)는 휴대폰에 전달하지 마세요. 90일 서버 인증서 만료·IP 변경 시 갱신이 필요합니다.

Safari에서 `https://<PC-LAN-IP>:8443`에 접속하고 페어링합니다. 인증서 경고가 남으면 먼저 신뢰/주소를 고쳐야 합니다. 보안 경고 무시나 브라우저 보안 해제는 사용하지 않습니다. 필요 시 Windows 방화벽 인바운드를 개인 네트워크·같은 LAN·8443 포트에 한정하여 직접 허용합니다. 자동 방화벽 변경·포트 포워딩·공개 터널은 하지 않습니다.

원복: 앱/STT 터미널 Ctrl+C, 기기에서 설치한 Language Conversation CA 프로파일 및 신뢰 항목 제거, 직접 추가한 방화벽 규칙 제거. 프로젝트 키는 재사용할 필요가 없을 때만 삭제합니다.

## 4. 대화

시나리오를 고르고 명시적으로 시작합니다. 영어 음성 재생 완료 후 자동 듣기가 열립니다. 1.5초 무음이 발화 종료 기본값, 최대 45초입니다. AI가 말할 때는 입력을 닫습니다. 잠금/다른 앱/오프라인에서는 일시정지하며 복귀 시 다시 시작을 직접 누릅니다. 마이크 거부·자동 재생 실패에는 버튼/텍스트 복구를 제공합니다. 수동 모드는 자동 음성 수용 기준의 대체 통과가 아닙니다.

브라우저 영어 음성은 기기의 voice 목록을 사용합니다. localService 표시와 실제 네트워크 처리는 별도 확인해야 하며 완전 온디바이스/오프라인을 보장하지 않습니다. 전사나 모델 프롬프트는 Ollama/STT loopback으로만 전달됩니다.

## 5. 데이터·마이그레이션·백업

SQLite 스키마는 `migrations/`에서 버전 관리하며 앱/페어링 실행 시 누락 마이그레이션을 적용합니다. 사용자 소유 데이터는 data 아래, 음성은 처리용 임시 영역만 사용합니다. 프로세스를 중지한 상태에서 DB와 auth-secret을 본인 백업 위치로 복사하세요. 복원은 같은 버전으로 먼저 시험하고 DB 파일을 원위치에 놓습니다. Secret을 바꾸면 기존 페어링을 다시 해야 합니다. 내보내기에는 개인정보가 포함될 수 있습니다.

설정의 내보내기/가져오기는 schema_version과 미리보기 확인 후 적용합니다. 전체 삭제 시 서버 학습 기록과 기기 앱 캐시를 지우지만 사용자가 별도 저장한 내보내기/백업은 제거할 수 없습니다. 삭제 전에 필요한 백업을 직접 보관하세요. 전체 디렉터리를 동기화 서비스에 공유하지 마세요.

## 6. 문제 해결

- 서버 연결 실패: PC 켜짐·같은 LAN·IP·인증서·8443 방화벽 범위를 확인합니다.
- Ollama 미가용: `ollama list`와 `http://127.0.0.1:11434/api/tags`를 PC에서 확인합니다.
- STT 미가용: STT 터미널 로그와 FFmpeg PATH, 8080 loopback 바인딩을 확인합니다.
- 응답이 느림: 본 PC에서 LLM 단독도 목표를 넘었습니다. 모델·출력 길이 개선 실험이 필요하며 현재 실시간 품질을 보장하지 않습니다.
- 마이크 없음: HTTPS·Safari 권한·다른 앱의 마이크 점유를 확인합니다.
- 음성 없음: 기기의 영어 voice 목록과 볼륨을 확인하고 직접 다시 듣기를 누릅니다.

실제 기기 확인은 [DEVICE_CHECKLIST.md](DEVICE_CHECKLIST.md)에 기기/OS/Safari/스피커·이어폰/30턴 지연을 남기세요. 사용자의 다음날·1주 지연 복습은 별도 실험이며 자동 테스트로 완료할 수 없습니다.

## 7. 개발 검증 재현

개발 테스트가 필요하면 .venv/Scripts/python -m pip install -r requirements-dev.lock.txt 후 .venv/Scripts/python -m pytest tests/test_learning_flow.py tests/test_api_security.py tests/test_product_boundaries.py --basetemp .runtime/pytest-local -q 를 실행합니다. 웹은 apps/web에서 npm test 와 npm run build 입니다. 실제 모델50개 검수는 python scripts/evaluate_tutor.py --output docs/evidence/my-quality.jsonl 을 실행한 뒤 python scripts/summarize_quality.py docs/evidence/my-quality.jsonl 로 집계합니다. 이 검사는 실제 모델을 호출하므로 본 PC에서는 약20분이 소요될 수 있습니다. 사람 검수나 실제 iPhone 검사의 대체가 아닙니다.


현재 작업 PC에는 `.venv`(패치 잠금 의존성), 웹 빌드, `.runtime`의 STT 바이너리/모델을 준비하고 `start.ps1 -LocalHttp -Port8002`와 `pair.ps1`의 실제 기동을 확인했습니다(`evidence/launcher-smoke.json`). 검사용 서버는 종료했고 기존 Ollama는 유지했습니다. 이 PC에서는 STT·앱 시작부터 다시 실행할 수 있습니다. 다른 PC/새 체크아웃에서는 처음의 의존성 설치 절차를 따르세요. 검사용 CA는 `.runtime/cert-final-verified`에 보존하고 루트 `.cert`에서는 치웠으므로, iPhone 연결 시 실제 LAN 주소로 인증서를 생성해야 합니다.
