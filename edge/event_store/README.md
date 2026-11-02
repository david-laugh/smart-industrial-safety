# edge/event_store/ — 로컬 이벤트 저장소 / Outbox

담당: **C (Edge / Cloud)** · 배포: `cloud-sync` · 상태: 구현 전

## 책임

- spool 수집: `/data/spool/incoming/*.json` 스키마 검증 → SQLite outbox에 `INSERT OR IGNORE`(PK=`event_id`) → `processed/`로 이동, 실패 시 `rejected/`
- Outbox 상태 관리: `PENDING` → `IMAGE_UPLOADED` → `SYNCED` / `DEAD_LETTER`
- 재시도 메타데이터: `attempts`, `next_attempt_at`, `last_error`
- 로컬 보존 정책: `EVENT_LOCAL_RETENTION_DAYS`, `EVENT_STORE_MAX_BYTES` (미전송 이벤트는 자동 삭제하지 않음)

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | [`ppe-event`](../../contracts/ppe-event.schema.json) (spool 파일) |
| 출력 | outbox 레코드(cloud_sync 내부용, 계약 범위 밖 — 구현 단계에서 C가 테이블 정의 문서화) |

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

- SQLite는 cloud-sync 프로세스만 연다(단일 작성자).
- 전원 차단 대비: WAL + `synchronous` 설정을 측정과 함께 결정, 전원 차단 테스트(SYS-REL-04)로 검증.
- 수집 방식(디렉터리 폴링 vs inotify)은 메모리·CPU 측정 후 결정.
