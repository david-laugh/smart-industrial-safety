# edge/event_store/ — 로컬 이벤트 저장소 / Outbox

담당: **C (Edge / Cloud)** · 배포: `cloud-sync` · 상태: 구현됨(PC 검증)

## 책임

- spool 수집: `/data/spool/incoming/*.json` 스키마 검증 → SQLite outbox에 `INSERT OR IGNORE`(PK=`event_id`) → `processed/`로 이동, 실패 시 `rejected/`
- Outbox 상태 관리: `PENDING` → `IMAGE_UPLOADED` → `SYNCED` / `DEAD_LETTER`
- 재시도 메타데이터: `attempts`, `next_attempt_at`, `last_error`
- 로컬 보존 정책: `EVENT_LOCAL_RETENTION_DAYS`, `EVENT_STORE_MAX_BYTES` (미전송 이벤트는 자동 삭제하지 않음)

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | [`ppe-event`](../../contracts/ppe-event.schema.json) (spool 파일, B `SpoolPublisher` 가 게시) |
| 출력 | outbox 레코드(cloud_sync 내부용, 계약 범위 밖 — 아래 표) |

## outbox 레코드 (잠정)

SQLite `outbox` 테이블(`outbox.py`). 통합 단계에서 다시 다듬는다.

| 컬럼 | 타입 | 내용 |
|---|---|---|
| `event_id` | TEXT PK | ppe-event `event_id` (재생성 금지) |
| `device_id` | TEXT | |
| `occurred_at` | TEXT | `timestamps.occurred_at_utc` 원문 |
| `occurred_at_ms` | INTEGER | 정렬용 epoch ms (업로드 순서 기준) |
| `image_path` | TEXT | `EVENT_DATA_ROOT` 기준 상대 경로 |
| `image_sha256` / `image_bytes` | TEXT / INTEGER | `image.sha256` / `image.byte_length` |
| `event_json` | TEXT | spool JSON 원문(→ `event_payload`) |
| `state` | TEXT | `PENDING` / `IMAGE_UPLOADED` / `SYNCED` / `DEAD_LETTER` |
| `attempts` | INTEGER | 재시도 가능 오류 횟수 |
| `next_attempt_at` | REAL | epoch s |
| `last_error` | TEXT | 최대 500자 |
| `created_at` / `updated_at` / `synced_at` | REAL | epoch s |
| `image_deleted_at` | REAL | 보존 정책으로 로컬 JPEG 삭제한 시각(행은 남김) |

## 수집 규칙

- `incoming/` 의 `*.json` 만 본다(`.part` 무시). 파일명 순, 1회 최대 200개.
- 거부(`rejected/` 로 이동 + 경고 로그): JSON 오류, ppe-event 스키마 실패, 파일명 ≠ `{event_id}.json`, JPEG 없음, 크기·sha256 불일치.
- DB 커밋 후 `processed/` 로 이동. 이동 전에 죽으면 다음 수집에서 중복(무시) 처리 후 이동.

## 보존 정책 (`retention.py`)

- `SYNCED` 후 `EVENT_LOCAL_RETENTION_DAYS` 지난 JPEG 와 `processed/{event_id}.json` 삭제.
- 로컬 JPEG 합계 > `EVENT_STORE_MAX_BYTES` 이면 `SYNCED` 오래된 것부터 삭제. 그래도 넘으면 `ALERT` 로그만(미전송은 보존).

## 데이터 배치

| 경로 | 내용 |
|---|---|
| `/data/spool/tmp/` | 원자 게시용 임시 파일 |
| `/data/spool/incoming/` | 수집 대기 이벤트 JSON |
| `/data/spool/processed/` | outbox 적재 완료 JSON |
| `/data/spool/rejected/` | 스키마 검증 실패 JSON |
| `/data/images/YYYY/MM/DD/` | 증거 JPEG |
| `/data/outbox.db` | SQLite (WAL 모드) |

## 설계 메모

- SQLite는 cloud-sync 프로세스만 연다(단일 작성자). WAL + `synchronous=FULL`(기본, `EVENT_STORE_SQLITE_SYNCHRONOUS`). 낮출지는 전원 차단 테스트(SYS-REL-04)와 측정 후 결정.
- 수집 방식은 디렉터리 폴링(`SYNC_POLL_INTERVAL_S`). inotify 전환은 메모리·CPU 측정 후 결정.
- 실행·환경변수·테스트: [cloud_sync/README.md](../cloud_sync/README.md)
