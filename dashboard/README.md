# dashboard/ — 이벤트 확인 웹 대시보드

담당: **C (Edge / Cloud)** · 상태: 1차 구현. Supabase 요청 형식은 대역(fake)으로 검증, 실제 dev 프로젝트 조회는 secret 키 설정 후 확인 필요

## 빠른 시작

```bash
cd dashboard
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m server
```

브라우저에서 http://127.0.0.1:8765/ 를 연다. `.env`가 없으면 브라우저에서 생성하는 **샘플 데이터**로 바로 보인다. `DASHBOARD_DATA_SOURCE=supabase`면 서버가 secret 키로 **실제 Supabase**를 조회하고, 화면은 접근 토큰(`/?token=…`)이 있어야 열린다([Supabase 연결](#supabase-연결)).

## 범위

- 로그인 화면 없음. 비인증 접근 차단(SYS-SEC-01)은 대시보드 접근 토큰(`DASHBOARD_ACCESS_TOKEN`)으로 판단. Supabase 는 anon 정책이 없어 publishable 키로는 아무것도 읽을 수 없음
- 이벤트 목록: 기간, 장치, 위반 유형 필터, `occurred_at` 내림차순
- 이벤트 상세: 증거 이미지(Signed URL), bbox 오버레이, 사람별 helmet/vest 상태와 신뢰도, 시간 정보(`occurred_at_source` 표시)
- 같은 프레임의 다른 위반: 같은 `frame_id` 이벤트를 상세 화면에 링크로 표시하고 이미지 위에 점선 bbox로 겹쳐 그림
- 읽기 전용 (이벤트 수정/삭제 기능 없음)

## 데이터 소스

| 데이터 | 출처 | 계약 |
|---|---|---|
| 이벤트 행 | Supabase `ppe_events` (서버가 secret 키로 select) | [`supabase-event-record`](../contracts/supabase-event-record.schema.json) |
| 이미지 | Storage `ppe-evidence` Signed URL(서버가 발급, 브라우저가 직접 받음) | [cloud/README.md](../cloud/README.md) §2 |

## 규칙

- 브라우저에는 **Supabase 키를 주지 않는다**. secret 키는 대시보드 서버(`.env` 의 `SUPABASE_SERVICE_KEY`)에만 있고, 서버는 정해진 읽기 질의만 한다.
- 서버가 털리면 secret 키(쓰기 포함 전체 권한)가 노출되므로 신뢰할 수 있는 호스트에서만 실행한다. 읽기 전용 DB 역할로 좁히는 것은 후속 과제.
- 시각은 UTC로 저장되어 있으므로 표시할 때만 현지 시간대로 변환한다.
- bbox는 0~1 정규화 좌표이므로 이미지 실제 크기에 곱해 그린다.

## 설계 결정 (C)

| 항목 | 결정 | 이유 |
|---|---|---|
| 프런트엔드 | 빌드 없는 정적 HTML + 바닐라 JS | 의존성·빌드 단계 없음 |
| 실행 서버 | FastAPI + uvicorn (`server/`) | 한 줄 실행, `.env`로 설정 주입, 보안 헤더, 서빙 범위 제한 |
| 데이터 접근 | `js/data-source.js` 어댑터(`sample` / `supabase`) | UI는 어댑터 인터페이스만 사용. `supabase` 는 대시보드 서버 `/api`(`server/supabase_api.py`)를 부르고, 서버가 secret 키로 Supabase 를 조회 |
| 실시간 갱신 | 선택(상단 "실시간" 토글). Supabase 모드는 서버 폴링(`ingested_at` 커서, 기본 15초) — Realtime 은 브라우저에 키가 필요해 쓰지 않음 | 기본은 꺼짐. 새 이벤트는 바로 끼워 넣지 않고 "새 이벤트 N건 보기"로 알림 |
| 딥링크 | `#event=<event_id>` | 특정 이벤트 링크 공유 |
| 접근 제어 | 접근 토큰 → httpOnly 쿠키(12시간) | 로그인 화면 없이 토큰으로 판단(SYS-SEC-01). API 는 `Authorization: Bearer` 도 허용 |
| 가데이터 | `seed/`(Python)가 생성해 cloud-sync 와 같은 규칙으로 업로드 | 대시보드가 실제 Supabase 통신으로 동작하는지 확인(I-5) |
| 호스팅 | 미정 | Supabase 프로젝트 확정 후 결정 |

## 구조

```
dashboard/
├── index.html                  화면 골격
├── css/style.css               스타일(라이트/다크)
├── js/
│   ├── config.js               기본 설정(dataSource="sample", 비밀 값 없음)
│   ├── sample-data.js          계약 형태 샘플 데이터 + SVG 증거 이미지 생성
│   ├── data-source.js          sample / supabase 어댑터
│   └── app.js                  UI(필터, 요약, 차트, 목록, 상세, 실시간)
├── server/
│   ├── main.py                 FastAPI 앱(정적 서빙, 설정 주입, 접근 토큰, 보안 헤더)
│   ├── supabase_api.py         /api 조회 API(secret 키로 PostgREST·Storage sign)
│   └── __main__.py             `python -m server` 진입점(uvicorn)
├── seed/
│   ├── schema.sql              dev 프로젝트용 테이블·버킷·RLS(anon 접근 없음)
│   ├── generate.py             계약 형태 가데이터 + 실제 JPEG 생성
│   └── __main__.py             `python -m seed` 업로더(Storage → ppe_events)
├── .env.example                서버/seed 환경변수 템플릿 → .env 로 복사(커밋 금지)
├── requirements.txt            실행 의존성(fastapi, uvicorn, python-dotenv, httpx)
├── requirements-dev.txt        테스트·seed 의존성(+ jsonschema, pytest, pillow, playwright)
└── tests/
    ├── conftest.py             uvicorn 서버·헤드리스 Chromium·계약 스키마 fixture
    ├── fake_supabase.py        PostgREST/Storage 대역(서버 → httpx, 브라우저 → 서명 이미지)
    ├── test_server.py          서빙 범위·설정 주입·키/토큰 검증·입력 검증·보안 헤더
    ├── test_sample_contract.py 샘플 데이터 ↔ contracts/ JSON Schema 검증(CON-01~03)
    ├── test_seed.py            가데이터 계약·JPEG·시간/ID/dedup 규칙·업로드 순서/헤더/재시도
    ├── test_supabase_mode.py   supabase 모드: 브라우저 → /api → Supabase 요청(필터·count·secret 키·Signed URL·폴링)
    └── test_dashboard_e2e.py   첫 화면·필터·페이지·상세·딥링크·실시간·모바일 E2E
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

`.env` 없이 실행하면 샘플 데이터로 동작한다. Supabase 에 연결하려면:

```bash
cp .env.example .env
```

### 3. 서버 실행

| 방법 | 명령 | 비고 |
|---|---|---|
| 기본 | `.venv/Scripts/python -m server` | `.env`의 `DASHBOARD_*` 값 사용 |
| 개발(자동 재시작) | `DASHBOARD_RELOAD=true .venv/Scripts/python -m server` | 파일 저장 시 서버 재시작. 브라우저는 새로고침 |
| uvicorn 직접 | `.venv/Scripts/python -m uvicorn server.main:create_app --factory --port 8765 --reload` | uvicorn 옵션을 그대로 쓰고 싶을 때 |
| 같은 네트워크 다른 기기에서 접속 | `DASHBOARD_BIND_ADDR=0.0.0.0 .venv/Scripts/python -m server` | 방화벽 허용 필요. 토큰이 평문 HTTP 로 오가므로 외부 공개 시 HTTPS 프록시 뒤에 둘 것 |
| 정적 서버(서버 없이) | `python -m http.server 8765` | 샘플 모드만. Supabase 모드는 서버가 필요 |

PowerShell에서는 `VAR=값 명령` 대신 `$env:DASHBOARD_RELOAD="true"; .venv\Scripts\python.exe -m server` 형식을 쓴다.

### 서버가 제공하는 경로

| 경로 | 내용 |
|---|---|
| `/` | `index.html` |
| `/css/*`, `/js/*` | 정적 파일. `.env`, `server/`, `tests/`, `.venv/` 등 그 밖의 파일은 404 |
| `/js/config.local.js` | 환경변수로 생성한 런타임 설정(`window.PPE_LOCAL_CONFIG`, 비밀 값 없음) |
| `/api/devices`, `/api/events`, `/api/stats`, `/api/events/{id}`, `/api/events/{id}/image`, `/api/frames/{frame_id}`, `/api/live` | supabase 모드 조회 API. 입력(시각·장치·유형·UUID·limit≤100)을 검증한 뒤에만 Supabase 로 보냄 |
| `/healthz` | `{"status":"ok","data_source":"sample"}` (토큰 불필요) |

`DASHBOARD_ACCESS_TOKEN` 이 설정되면 `/healthz` 외 모든 경로가 토큰을 요구한다(없으면 401). `/?token=…` 으로 열면 httpOnly·SameSite=Strict 쿠키로 바꾸고 주소에서 토큰을 지운다(303).

모든 응답에 `Cache-Control: no-cache`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`(Signed URL 유출 방지), `Content-Security-Policy`(스크립트·API 는 자기 출처만, 이미지는 자기 출처 + 설정된 Supabase 호스트)를 붙인다. OpenAPI 문서(`/docs`)는 끈다.

## 환경변수

셸 환경변수가 `dashboard/.env` 보다 우선한다. 이름은 [infra/docker/.env.example](../infra/docker/.env.example) 규칙을 따른다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DASHBOARD_BIND_ADDR` | `127.0.0.1` | 바인드 주소 |
| `DASHBOARD_PORT` | `8765` | 포트(Gateway 8080과 겹치지 않게) |
| `DASHBOARD_RELOAD` | `false` | `true`면 코드 변경 시 자동 재시작(개발용) |
| `LOG_LEVEL` | `INFO` | uvicorn 로그 레벨 |
| `DASHBOARD_DATA_SOURCE` | `sample` | `sample` 또는 `supabase` |
| `SUPABASE_URL` | — | `supabase` 모드 필수, `https://` |
| `DASHBOARD_ACCESS_TOKEN` | — | `supabase` 모드 필수, 16자 이상. 샘플 모드에서도 설정하면 적용 |
| `SUPABASE_SERVICE_KEY` | — | `supabase` 모드 필수. secret(`sb_secret_…`) 키 값(`.env`, 커밋 금지). RLS 를 우회해 테이블·버킷을 읽는다. publishable/anon 키면 시작 거부 |
| `SUPABASE_EVENTS_TABLE` | `ppe_events` | 조회 테이블 |
| `SUPABASE_STORAGE_BUCKET` | `ppe-evidence` | 증거 이미지 버킷 |
| `DASHBOARD_SIGNED_URL_EXPIRES_S` | `120` | Signed URL 만료(초) |
| `DASHBOARD_LIVE_INTERVAL_S` | `15` | 실시간 토글 폴링 주기(초) |

설정이 잘못되면(알 수 없는 데이터 소스, URL/키/토큰 누락, 짧은 토큰, http URL, publishable 키를 secret 자리에) 서버가 시작 시점에 오류를 내고 멈춘다. `SUPABASE_ANON_KEY` 는 더 이상 쓰지 않는다.

## 샘플 데이터 (`DASHBOARD_DATA_SOURCE=sample`, 기본)

- 4개 장치, 최근 30일 180건이 seed로 결정적으로 생성된다(정시 단위로 고정되어 새로고침해도 `event_id` 유지).
- 샘플 데이터는 `supabase-event-record` / `ppe-event` 스키마를 통과한다(테스트로 보장).

## Supabase 연결

### 1. dev 프로젝트 준비 (최초 1회)

Supabase **dev** 프로젝트(운영과 분리, [docs/integration-plan.md](../docs/integration-plan.md) §6)의 SQL Editor에서 [`seed/schema.sql`](seed/schema.sql)을 실행한다. 여러 번 실행해도 안전하다.

- `public.ppe_events` 테이블·인덱스([cloud/README.md](../cloud/README.md) §1과 동일)
- 비공개 버킷 `ppe-evidence`(1 MiB, `image/jpeg`만)
- RLS 켬, `anon`/`authenticated` 정책 **없음** → publishable 키로는 행·객체 모두 접근 불가([cloud/README.md](../cloud/README.md) §3). 조회(대시보드 서버)와 업로드(cloud-sync, seed)는 secret 키(RLS 우회)
- 이전 버전이 만든 anon 조회 정책과 Realtime publication 은 제거

### 2. 가데이터 업로드

```bash
cd dashboard
.venv/Scripts/python -m pip install -r requirements-dev.txt
# .env: SUPABASE_URL, SUPABASE_SERVICE_KEY(secret 키)
.venv/Scripts/python -m seed --count 60 --days 7          # 최근 7일에 60건
.venv/Scripts/python -m seed --count 10 --out seed-out    # 업로드 없이 JSON/JPEG 로만 생성
```

- cloud-sync 와 같은 규칙으로 올린다: `occurred_at` 오래된 순, 이벤트마다 **Storage 먼저 → 행 나중**.
- 업로드 전에 모든 행을 `contracts/supabase-event-record` 로 검증(CON-01). 하나라도 실패하면 아무것도 올리지 않는다.
- 같은 `--seed` 로 다시 실행해도 중복이 생기지 않는다(Storage 409 + 해시 일치는 성공, 행은 `event_id` 중복 무시). 단 시각이 실행 시점 기준이라 다른 시각에 실행하면 새 이벤트가 된다.
- 같은 secret 키를 대시보드 서버도 읽지만 브라우저로는 내려보내지 않는다(`config.local.js`·HTML 에 없음, 테스트로 확인).
- 지우려면 dev 프로젝트에서 `truncate public.ppe_events;` 와 Storage `ppe-evidence` 비우기.

### 3. 대시보드 연결

`.env` 에 다음을 넣고 서버를 재시작한다.

```
DASHBOARD_DATA_SOURCE=supabase
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<sb_secret_… 키>
DASHBOARD_ACCESS_TOKEN=<아래 명령 결과>
```

```bash
.venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

그다음 `http://127.0.0.1:8765/?token=<DASHBOARD_ACCESS_TOKEN>` 으로 한 번 연다(이후 12시간 쿠키).

secret 키로 DB 에 접근되는지만 먼저 확인(서버 실행 불필요, `.env` 의 `SUPABASE_URL`·`SUPABASE_SERVICE_KEY` 사용):

```bash
.venv/Scripts/python -m server check
# [성공] 테이블 ppe_events: 접근 가능 (행 N건)
# [성공] 버킷 ppe-evidence: 접근 가능
```

서버를 띄운 뒤 API 로 확인:

```bash
curl -H "Authorization: Bearer <DASHBOARD_ACCESS_TOKEN>" http://127.0.0.1:8765/api/devices
```

`[]` 또는 장치 목록이면 정상. `502 … secret 키` 면 키 문제, `502 … PGRST205` 면 schema.sql 미실행.

### 주고받는 데이터 규칙

기준 문서: [contracts/supabase-event-record.schema.json](../contracts/supabase-event-record.schema.json)(스키마가 문서보다 우선), [docs/protocols.md](../docs/protocols.md) §5·시간 규칙·ID 규칙·중복 방지, [cloud/README.md](../cloud/README.md).

| 구분 | 규칙 |
|---|---|
| 행 | `supabase-event-record` 15개 필드 전부 필수, 추가 필드 금지. `ingested_at` 은 DB가 생성(보내지 않음) |
| 원본 | `event_payload` = `ppe-event` 전체(jsonb). `violation_count`=`violations` 길이, `violation_types`=사람별 유형 합집합 |
| 이벤트 단위 | **위반자 1명 = 이벤트 1건**(B 파트 결정, `dedup_key` 가 사람 1명 기준). 따라서 `violation_count`=1, `violations` 1명. 한 프레임에 위반자가 여럿이면 같은 `frame_id`·시각·JPEG(`image_sha256` 동일)로 이벤트가 여러 건 생긴다 |
| 시각 | RFC 3339 UTC, `Z` 필수(`+09:00` 금지). 표시할 때만 현지 시간대로 변환 |
| `occurred_at` | `capture_ts_utc` 가 있고 `gateway_received_at` 과 차이 ≤ `TIME_MAX_DEVICE_SKEW_MS`(2000) → `DEVICE_SNTP`, 아니면 수신 시각 + `GATEWAY_RECEIVED` |
| ID | `event_id`·`frame_id` UUIDv7 소문자. `event_id` 는 영구 불변(재시도·재업로드에도 동일, PK) |
| PPE 상태 | `WORN`/`MISSING`/`UNKNOWN`. UNKNOWN 만 있는 사람은 위반 아님. 위반 유형 `HELMET_MISSING`/`VEST_MISSING` |
| bbox | 원본 이미지 기준 0~1 정규화, 좌상단 원점 |
| `dedup_key` | `{device_id}\|{track_id 또는 pos:r{행}c{열}}\|{정렬된 유형을 ,로}\|{window_start_epoch_s}` |
| 이미지 | 비공개 버킷 `ppe-evidence`, 경로 `{device_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg`(occurred_at UTC 날짜), `image/jpeg`, ≤ 1 MiB, `image_sha256` = 실제 바이트 SHA-256. JSON에 Base64 금지 |
| 업로드 | Storage `POST /storage/v1/object/ppe-evidence/{path}` + `x-upsert: false` → `POST /rest/v1/ppe_events?on_conflict=event_id` + `Prefer: resolution=ignore-duplicates,return=minimal` |
| 재시도 | 408/429/5xx·네트워크 → 백오프(`Retry-After` 우선), 400/422 → 재시도 안 함, 401/403 → 즉시 중단 |
| Storage 용량 | 같은 JPEG 가 `event_id` 경로마다 따로 저장된다 → 용량 ≈ **이벤트 수 × JPEG 크기**(프레임 수가 아님). 요약의 "증거 프레임" 타일로 이벤트 수와 실제 프레임 수 차이를 볼 수 있다 |
| 대시보드 조회 | 서버가 secret 키로 `select`: `occurred_at` gte/lt, `device_id=in.(…)`, `violation_types=ov.{…}`, `order=occurred_at.desc`, `offset/limit` + `Prefer: count=exact`. 이미지는 `POST /storage/v1/object/sign/…`(기본 120초) |
| 접근 | 브라우저 → 대시보드 서버: 접근 토큰. 서버·cloud-sync·seed → Supabase: secret 키. publishable 키로는 접근 불가 |

미결 사항(TODO):
- 장치 목록: 지금은 최근 1000행에서 추출 → 장치 테이블/뷰가 생기면 교체
- 요약/차트: 경량 컬럼을 최대 5000행 조회해 클라이언트에서 집계 → 데이터가 늘면 집계 RPC/뷰로 교체
- 정식 마이그레이션은 `cloud/` 로 옮긴다(`seed/schema.sql` 은 dev 전용)
- 같은 프레임 조회(`event_payload->>frame_id`)는 지금 인덱스가 없다 → 행이 늘면 식 인덱스 또는 `frame_id` 컬럼 추가(계약 변경이므로 [integration-plan](../docs/integration-plan.md) §5 절차)
- 같은 JPEG 중복 저장을 줄이려면 Storage 경로를 프레임 기준으로 바꿔야 하는데, 이는 `storage_path` 계약 변경이다(C + D 승인)

## 테스트

```bash
cd dashboard
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m playwright install chromium     # 최초 1회
.venv/Scripts/python -m pytest tests -q
```

- `test_server.py`: 허용 경로 외 파일이 노출되지 않는지, 설정에 비밀 값이 없는지, publishable 키 거부, 토큰 → 쿠키·401, API 입력 검증(잘못된 값은 Supabase 로 안 감), 잘못된 설정 즉시 실패, 보안 헤더, 환경변수 파싱.
- `test_sample_contract.py`: 브라우저에서 `js/sample-data.js`를 실행해 얻은 행을 `contracts/supabase-event-record`, `ppe-event` 스키마로 검증(`jsonschema` + `$ref` 레지스트리). 비정규화 컬럼 일치, `storage_path` 날짜 규칙, 시각 출처 규칙, 결정성도 확인한다. 계약이 바뀌면 이 테스트가 먼저 깨진다.
- `test_dashboard_e2e.py`: 실제 FastAPI 앱(uvicorn, 임의 포트, 샘플 모드)을 헤드리스 Chromium으로 연다. JS 예외나 콘솔 오류(CSP 위반·404 포함)가 나면 실패한다.
- `test_seed.py`: seed 가데이터가 계약·시간·ID·dedup 규칙을 지키는지, JPEG 메타데이터 일치, 업로드 순서·헤더·409 해시 비교·401 중단(네트워크는 `httpx.MockTransport`).
- `test_supabase_mode.py`: supabase 모드 서버가 Supabase 로 보내는 요청(secret 키 헤더·필터·count·sign)을 `fake_supabase.py`(httpx 대역)가 받아 검증하고, 브라우저는 서명 이미지 외에 Supabase 로 직접 가지 않는지, 키가 화면·설정에 없는지, 폴링으로 새 행을 알리는지 확인한다. 실제 Supabase 프로젝트와의 통신은 아니다.
- 테스트는 로컬 `.env` 와 무관하게 기본 설정(샘플)으로 돈다.
