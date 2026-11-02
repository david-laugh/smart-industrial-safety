# contracts/ — 인터페이스 계약

컴포넌트 간 데이터 형식의 **단일 기준(Single Source of Truth)**. 모든 스키마는 JSON Schema **draft 2020-12**를 따른다.

- 관리 책임: **D (Infrastructure / QA / Integration)** — 리뷰, 버전 관리, 계약 테스트
- 내용 책임: 각 인터페이스의 **생산자** 역할

## 스키마 목록

| 파일 | 인터페이스 | 생산자 → 소비자 | 전송 형태 |
|---|---|---|---|
| `common.schema.json` | 공통 타입(`$defs`) | — | — |
| `esp32-frame-metadata.schema.json` | 1. ESP32 → Gateway | A → C | multipart `metadata` 파트 (JPEG는 `image` 파트) |
| `gateway-response.schema.json` | 1. 응답 Gateway → ESP32 | C → A | HTTP 응답 본문 |
| `edge-frame.schema.json` | 2. Gateway → Inference | C → B | 프로세스 내부 객체 (3-컨테이너 시 UDS 직렬화) |
| `inference-result.schema.json` | 3. Inference → PPE Decision | B → B | 프로세스 내부 객체 |
| `ppe-event.schema.json` | 4. Decision → Cloud Sync | B → C | spool 디렉터리의 JSON 파일 + JPEG 파일 |
| `supabase-event-record.schema.json` | 5. Cloud Sync → Supabase | C → Supabase/대시보드 | PostgREST upsert 본문 |

`examples/`에는 각 스키마를 통과해야 하는 예시 인스턴스가 있다. 파일명 접두사(`<이름>.`)가 스키마 이름과 같다.

> 예시의 숫자(신뢰도, 바이트 수, `timing`의 0 값, 0으로 채운 sha256 등)는 **형식 설명용 자리표시자**이며 측정값이나 모델 성능이 아니다.

## 공통 규칙

| 규칙 | 내용 |
|---|---|
| PPE 상태 | `WORN` / `MISSING` / `UNKNOWN` 만 사용 |
| 시각 | RFC 3339 UTC, `Z` 접미사 필수 (`+09:00` 금지) |
| MCU 시간 | `mcu_uptime_ms`(단조 증가) + `boot_id`(부팅 구분). 벽시계로 간주하지 않음 |
| 수신 시각 | `gateway_received_at_utc`는 Raspberry Pi 시계 기준, `gateway_clock_synced`로 동기화 여부 표기 |
| 이미지 | 이미지 바이트는 JSON에 넣지 않는다(Base64 금지). multipart 파트, 파일 경로, 스트림 프레임으로만 전달 |
| event_id | UUID(v7 권장). Decision에서 1회 생성, 저장·재시도·재업로드 시 불변 |
| bbox | 원본 이미지 기준 0~1 정규화, 좌상단 원점 |
| 알 수 없는 필드 | `additionalProperties: false` — 생산자는 정의되지 않은 필드를 보내지 않는다 |

## 버전 규칙

- 모든 메시지에 `schema_version`(SemVer, 현재 `1.0.0`) 포함. 단, Supabase 행은 컬럼으로 포함.
- **PATCH**: 설명·예시 수정, 검증 의미 변화 없음.
- **MINOR**: 선택(optional) 필드 추가 등 하위 호환 변경. 소비자를 먼저 배포한다.
- **MAJOR**: 필드 삭제/이름 변경/의미 변경/enum 값 제거. `$id` 경로에 버전 디렉터리를 추가하고 병행 기간을 둔다.
- 변경 절차는 [docs/integration-plan.md](../docs/integration-plan.md) §계약 변경 절차.

## 검증

현재 저장소에는 검증 도구나 스크립트를 두지 않는다. 계약 테스트 구현 계획은 [tests/contract/README.md](../tests/contract/README.md)에 있다.
초기 스켈레톤 작성 시 저장소 밖의 임시 검사기로 다음을 확인했다:

- 7개 스키마 JSON 파싱, `$schema`=2020-12, `$id`와 파일명 일치, 모든 `$ref` 해석 가능, 정규식 컴파일 가능
- `examples/` 8개 인스턴스 통과
- 부정 케이스 거부: Base64 이미지 필드 추가, SNTP 동기화인데 `capture_ts_utc` 없음, 비 UTC 시각, 잘못된 status enum, 위반 0건 이벤트, 절대/상위 경로 `image_path`, 거부 응답에 `frame_id` 포함
