# dashboard/ — 이벤트 확인 웹 대시보드

담당: **C (Edge / Cloud)** · 상태: 1차 구현(목업 데이터로 동작·테스트, Supabase 연결은 미검증)

## 빠른 시작

```bash
cd dashboard
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m server
```

브라우저에서 http://127.0.0.1:8765/ 를 연다. 기본은 **목업 모드**라 아무 이메일로 로그인하면 된다.

## 범위

- 로그인(인증 사용자만)
- 이벤트 목록: 기간, 장치, 위반 유형 필터, `occurred_at` 내림차순
- 이벤트 상세: 증거 이미지(Signed URL), bbox 오버레이, 사람별 helmet/vest 상태와 신뢰도, 시간 정보(`occurred_at_source` 표시)
- 읽기 전용 (이벤트 수정/삭제 기능 없음)

## 데이터 소스

| 데이터 | 출처 | 계약 |
|---|---|---|
| 이벤트 행 | Supabase `ppe_events` (select) | [`supabase-event-record`](../contracts/supabase-event-record.schema.json) |
| 이미지 | Storage `ppe-evidence` Signed URL | [cloud/README.md](../cloud/README.md) §2 |

## 규칙

- 클라이언트에는 **anon key만** 사용한다. service_role 키를 대시보드에 넣지 않는다(서버가 시작 시 거부).
- 시각은 UTC로 저장되어 있으므로 표시할 때만 현지 시간대로 변환한다.
- bbox는 0~1 정규화 좌표이므로 이미지 실제 크기에 곱해 그린다.

## 설계 결정 (C)

| 항목 | 결정 | 이유 |
|---|---|---|
| 프런트엔드 | 빌드 없는 정적 HTML + 바닐라 JS | 의존성·빌드 단계 없음 |
| 실행 서버 | FastAPI + uvicorn (`server/`) | 한 줄 실행, `.env`로 설정 주입, 보안 헤더, 서빙 범위 제한 |
| 데이터 접근 | `js/data-source.js` 어댑터(`mock` / `supabase`) | UI는 어댑터 인터페이스만 사용 → Supabase 준비 전에도 UI 개발 가능. 서버는 데이터 API를 갖지 않고 브라우저가 anon key로 직접 조회 |
| 실시간 갱신 | 선택(상단 "실시간" 토글). Supabase에서는 Realtime `INSERT` 구독 | 기본은 꺼짐. 새 이벤트는 바로 끼워 넣지 않고 "새 이벤트 N건 보기"로 알림 |
| 딥링크 | `#event=<event_id>` | 특정 이벤트 링크 공유 |
| 호스팅·계정 관리 | 미정 | Supabase 프로젝트 확정 후 결정 |

## 구조

```
dashboard/
├── index.html                  화면 골격
├── css/style.css               스타일(라이트/다크)
├── js/
│   ├── config.js               기본 설정(dataSource="mock", 비밀 값 없음)
│   ├── config.local.example.js 정적 호스팅용 설정 템플릿(서버 사용 시 불필요)
│   ├── mock-data.js            계약 형태 가데이터 + SVG 증거 이미지 생성
│   ├── data-source.js          mock / supabase 어댑터
│   └── app.js                  UI(필터, 요약, 차트, 목록, 상세, 실시간)
├── server/
│   ├── main.py                 FastAPI 앱(정적 서빙, 설정 주입, 보안 헤더)
│   └── __main__.py             `python -m server` 진입점(uvicorn)
├── .env.example                서버 환경변수 템플릿 → .env 로 복사(커밋 금지)
├── requirements.txt            실행 의존성(fastapi, uvicorn, python-dotenv)
├── requirements-dev.txt        테스트 의존성(+ jsonschema, pytest, httpx, playwright)
└── tests/
    ├── conftest.py             uvicorn 서버·헤드리스 Chromium·계약 스키마 fixture
    ├── test_server.py          서빙 범위·설정 주입·키 검증·보안 헤더
    ├── test_mock_contract.py   목업 데이터 ↔ contracts/ JSON Schema 검증
    └── test_dashboard_e2e.py   로그인·필터·페이지·상세·딥링크·실시간·모바일 E2E
```

## 실행

### 1. 설치 (최초 1회, Python 3.12+)

```bash
cd dashboard
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

Windows PowerShell에서는 `.venv\Scripts\python.exe`, macOS/Linux에서는 `.venv/bin/python` 을 쓴다. 아래 명령은 Windows Git Bash 기준이다.

### 2. 설정 (선택)

목업 모드는 설정 없이 동작한다. 바꿀 값이 있으면:

```bash
cp .env.example .env
```

### 3. 서버 실행

| 방법 | 명령 | 비고 |
|---|---|---|
| 기본 | `.venv/Scripts/python -m server` | `.env`의 `DASHBOARD_*` 값 사용 |
| 개발(자동 재시작) | `DASHBOARD_RELOAD=true .venv/Scripts/python -m server` | 파일 저장 시 서버 재시작. 브라우저는 새로고침 |
| uvicorn 직접 | `.venv/Scripts/python -m uvicorn server.main:create_app --factory --port 8765 --reload` | uvicorn 옵션을 그대로 쓰고 싶을 때 |
| 같은 네트워크 다른 기기에서 접속 | `DASHBOARD_BIND_ADDR=0.0.0.0 .venv/Scripts/python -m server` | 방화벽 허용 필요. 인증 없는 목업 모드를 외부에 열지 않도록 주의 |
| 정적 서버(서버 없이) | `python -m http.server 8765` | `.env` 미적용. Supabase 값은 `js/config.local.js`(커밋 금지)로 |

PowerShell에서는 `VAR=값 명령` 대신 `$env:DASHBOARD_RELOAD="true"; .venv\Scripts\python.exe -m server` 형식을 쓴다.

### 서버가 제공하는 경로

| 경로 | 내용 |
|---|---|
| `/` | `index.html` |
| `/css/*`, `/js/*` | 정적 파일. `.env`, `server/`, `tests/`, `.venv/` 등 그 밖의 파일은 404 |
| `/js/config.local.js` | 환경변수로 생성한 런타임 설정(`window.PPE_LOCAL_CONFIG`) |
| `/healthz` | `{"status":"ok","data_source":"mock"}` |

모든 응답에 `Cache-Control: no-cache`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`(Signed URL 유출 방지), `Content-Security-Policy`(자기 출처 + supabase-js CDN + 설정된 Supabase 호스트만 허용)를 붙인다. OpenAPI 문서(`/docs`)는 끈다.

## 환경변수

셸 환경변수가 `dashboard/.env` 보다 우선한다. 이름은 [infra/docker/.env.example](../infra/docker/.env.example) 규칙을 따른다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DASHBOARD_BIND_ADDR` | `127.0.0.1` | 바인드 주소 |
| `DASHBOARD_PORT` | `8765` | 포트(Gateway 8080과 겹치지 않게) |
| `DASHBOARD_RELOAD` | `false` | `true`면 코드 변경 시 자동 재시작(개발용) |
| `LOG_LEVEL` | `INFO` | uvicorn 로그 레벨 |
| `DASHBOARD_DATA_SOURCE` | `mock` | `mock` 또는 `supabase` |
| `SUPABASE_URL` | — | `supabase` 모드 필수, `https://` |
| `SUPABASE_ANON_KEY` | — | `supabase` 모드 필수. **anon/publishable 키만**. service_role JWT나 `sb_secret_` 키면 서버가 시작을 거부 |
| `SUPABASE_EVENTS_TABLE` | `ppe_events` | 조회 테이블 |
| `SUPABASE_STORAGE_BUCKET` | `ppe-evidence` | 증거 이미지 버킷 |
| `DASHBOARD_SIGNED_URL_EXPIRES_S` | `120` | Signed URL 만료(초) |

설정이 잘못되면(알 수 없는 데이터 소스, URL/키 누락, http URL, 고권한 키) 서버가 시작 시점에 오류를 내고 멈춘다.

## 목업 모드

- 아무 이메일로 로그인(비밀번호 확인 없음).
- 4개 장치, 최근 30일 180건이 seed로 결정적으로 생성된다(정시 단위로 고정되어 새로고침해도 `event_id` 유지).
- 목업 데이터는 `supabase-event-record` / `ppe-event` 스키마를 통과한다(테스트로 보장).

## Supabase 연결 (준비되면)

1. `.env`에 `DASHBOARD_DATA_SOURCE=supabase`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` 입력 후 서버 재시작
2. 전제: `ppe_events` select·Storage Signed URL 발급이 authenticated 사용자에게 허용(RLS, [cloud/README.md](../cloud/README.md) §3), Realtime을 쓰려면 `ppe_events`를 publication에 추가, 대시보드 사용자 계정(이메일/비밀번호) 생성
3. 어댑터가 사용하는 쿼리: `occurred_at` 범위(gte/lt), `device_id in`, `violation_types` overlaps, `occurred_at desc` + range 페이지네이션, `count=exact`

미결 사항(TODO):
- 장치 목록: 지금은 최근 1000행에서 추출 → 장치 테이블/뷰가 생기면 교체
- 요약/차트: 경량 컬럼을 최대 5000행 조회해 클라이언트에서 집계 → 데이터가 늘면 집계 RPC/뷰로 교체

## 테스트

```bash
cd dashboard
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m playwright install chromium     # 최초 1회
.venv/Scripts/python -m pytest tests -q
```

- `test_server.py`: 허용 경로 외 파일이 노출되지 않는지, 설정 주입, 고권한 키 거부, 잘못된 설정 즉시 실패, 보안 헤더, 환경변수 파싱.
- `test_mock_contract.py`: 브라우저에서 `js/mock-data.js`를 실행해 얻은 행을 `contracts/supabase-event-record`, `ppe-event` 스키마로 검증(`jsonschema` + `$ref` 레지스트리). 비정규화 컬럼 일치, `storage_path` 날짜 규칙, 시각 출처 규칙, 결정성도 확인한다. 계약이 바뀌면 이 테스트가 먼저 깨진다.
- `test_dashboard_e2e.py`: 실제 FastAPI 앱(uvicorn, 임의 포트, 목업 모드)을 헤드리스 Chromium으로 연다. JS 예외나 콘솔 오류(CSP 위반·404 포함)가 나면 실패한다.
- 테스트는 로컬 `.env` 와 무관하게 기본 설정(목업)으로 돈다.
