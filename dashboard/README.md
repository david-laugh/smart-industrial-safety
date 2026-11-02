# dashboard/ — 이벤트 확인 웹 대시보드

담당: **C (Edge / Cloud)** · 상태: 1차 구현(목업 데이터로 동작·테스트, Supabase 어댑터는 미검증)

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

- 클라이언트에는 **anon key만** 사용한다. service_role 키를 대시보드에 넣지 않는다.
- 시각은 UTC로 저장되어 있으므로 표시할 때만 현지 시간대로 변환한다.
- bbox는 0~1 정규화 좌표이므로 이미지 실제 크기에 곱해 그린다.

## 설계 결정 (C)

| 항목 | 결정 | 이유 |
|---|---|---|
| 프레임워크 | 빌드 없는 정적 HTML + 바닐라 JS | 의존성·빌드 단계 없음, 어떤 정적 호스팅에도 그대로 배포 |
| 데이터 접근 | `js/data-source.js` 어댑터(`mock` / `supabase`) | UI는 어댑터 인터페이스만 사용 → Supabase 준비 전에도 UI 개발 가능 |
| 실시간 갱신 | 선택(상단 "실시간" 토글). Supabase에서는 Realtime `INSERT` 구독 | 기본은 꺼짐. 새 이벤트는 바로 끼워 넣지 않고 "새 이벤트 N건 보기"로 알림 |
| 딥링크 | `#event=<event_id>` | 특정 이벤트 링크 공유 |
| 호스팅·계정 관리 | 미정 | Supabase 프로젝트 확정 후 결정 |

## 구조

```
dashboard/
├── index.html                  화면 골격
├── css/style.css               스타일(라이트/다크)
└── js/
    ├── config.js               기본 설정(dataSource="mock", 비밀 값 없음)
    ├── config.local.example.js 실제 Supabase 값 템플릿 → config.local.js 로 복사(커밋 금지)
    ├── mock-data.js            계약 형태 가데이터 + SVG 증거 이미지 생성
    ├── data-source.js          mock / supabase 어댑터
    └── app.js                  UI(필터, 요약, 차트, 목록, 상세, 실시간)
requirements-dev.txt            개발/테스트 전용 Python 의존성
tests/
├── conftest.py                 정적 서버·헤드리스 Chromium·계약 스키마 fixture
├── test_mock_contract.py       목업 데이터 ↔ contracts/ JSON Schema 검증
└── test_dashboard_e2e.py       로그인·필터·페이지·상세·딥링크·실시간·모바일 E2E
```

## 실행

정적 파일 서버로 `dashboard/`를 열면 된다(파일을 직접 여는 `file://`은 브라우저에 따라 스크립트가 막힐 수 있음).
대시보드 자체는 Python 의존성이 없다(표준 라이브러리 서버만 사용).

```bash
cd dashboard && python -m http.server 8765
```

## 테스트 (Python 3.12+)

```bash
cd dashboard
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python -m pytest tests -q
```

- `test_mock_contract.py`: 브라우저에서 `js/mock-data.js`를 실행해 얻은 행을 `contracts/supabase-event-record`, `ppe-event` 스키마로 검증(`jsonschema` + `$ref` 레지스트리). 비정규화 컬럼 일치, `storage_path` 날짜 규칙, 시각 출처 규칙, 결정성도 확인한다. 계약이 바뀌면 이 테스트가 먼저 깨진다.
- `test_dashboard_e2e.py`: 목업 모드에서 화면 흐름 확인. JS 예외가 나면 실패한다.
- `.venv/`는 루트 `.gitignore`로 이미 제외된다.

- 목업 모드(기본): 아무 이메일로 로그인. 4개 장치, 최근 30일 180건이 seed로 결정적으로 생성된다(정시 단위로 고정되어 새로고침해도 `event_id` 유지).
- 목업 데이터는 `supabase-event-record` 스키마의 패턴(UUID, UTC 시각, `storage_path`, bbox 범위, `violation_types` 합집합 등)을 따른다.

## Supabase 연결 (준비되면)

1. `js/config.local.example.js` → `js/config.local.js` 로 복사하고 URL / anon key 입력
2. 전제: `ppe_events` select·Storage Signed URL 발급이 authenticated 사용자에게 허용(RLS, [cloud/README.md](../cloud/README.md) §3), Realtime을 쓰려면 `ppe_events`를 publication에 추가
3. 어댑터가 사용하는 쿼리: `occurred_at` 범위(gte/lt), `device_id in`, `violation_types` overlaps, `occurred_at desc` + range 페이지네이션, `count=exact`

미결 사항(TODO):
- 장치 목록: 지금은 최근 1000행에서 추출 → 장치 테이블/뷰가 생기면 교체
- 요약/차트: 경량 컬럼을 최대 5000행 조회해 클라이언트에서 집계 → 데이터가 늘면 집계 RPC/뷰로 교체

## 환경변수

`SUPABASE_URL`, `SUPABASE_ANON_KEY` 에 해당하는 값은 `js/config.local.js` 에만 둔다 — 커밋하지 않는다.
