# edge/cloud_sync/ — Supabase 동기화

담당: **C (Edge / Cloud)** · 배포: `cloud-sync` (mem_limit 192m) · 상태: 구현됨(PC 검증, 대역 Supabase)

> 입출력 규격 중 계약(contracts/) 밖의 부분(outbox 레코드, 추가 환경변수, 404 처리)은 **잠정 확정**이다. 통합 단계에서 다시 다듬는다.

## 책임

- outbox `PENDING` 이벤트를 `occurred_at` 오래된 순으로 업로드
- 1단계 Storage 업로드(비공개 버킷, `x-upsert: false`, 409 시 해시 확인) → 2단계 `ppe_events` upsert(`on_conflict=event_id`, ignore-duplicates)
- 재시도 분류, 지수 백오프 + 지터, `Retry-After` 준수
- 401/403 시 업로드 일시 중지 + 경보
- `event_id`를 절대 재생성하지 않음

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | outbox 레코드([`edge/event_store`](../event_store/README.md#outbox-레코드-잠정)) + 로컬 JPEG `EVENT_DATA_ROOT/{image_path}` |
| 출력 | [`supabase-event-record`](../../contracts/supabase-event-record.schema.json), Storage 객체 `{device_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg` |

ppe-event → 행 변환(`record.py`): 원본 값만 쓴다.

| 행 컬럼 | 원본 |
|---|---|
| `event_id`, `schema_version`, `device_id` | 같은 이름 |
| `occurred_at` / `occurred_at_source` / `gateway_received_at` / `decided_at` | `timestamps.*_utc` |
| `violation_count` / `violation_types` | `len(violations)` / 합집합 정렬 |
| `storage_bucket` / `storage_path` | `SUPABASE_STORAGE_BUCKET` / occurred_at UTC 날짜 경로 |
| `image_sha256` | `image.sha256` |
| `model_name` / `model_version` | `model.name` / `model.version` |
| `event_payload` | ppe-event 원문 전체 |

상세: [docs/protocols.md §5](../../docs/protocols.md)

## 동작

```
spool/incoming/*.json ─(event_store 수집)─▶ outbox PENDING
PENDING ── 로컬 JPEG sha 확인 → Storage POST ──▶ IMAGE_UPLOADED ── ppe_events upsert ──▶ SYNCED
```

| 응답 | 분류 | outbox |
|---|---|---|
| 2xx | 성공 | 다음 단계 |
| Storage 409(본문 `statusCode:"409"` 포함) + 원격 sha256 일치 | 성공 | IMAGE_UPLOADED |
| Storage 409 + 해시 불일치 | 재시도 불가 | DEAD_LETTER |
| 네트워크 오류·타임아웃, 408/429/5xx | 재시도 | 상태 유지, `attempts+1`, `next_attempt_at` = `Retry-After` 또는 백오프 |
| 400/422 등 그 밖의 4xx | 재시도 불가 | DEAD_LETTER |
| 401/403 | 설정 오류 | **전체 일시 중지** `SYNC_AUTH_PAUSE_S`, 경보 로그, 이벤트는 그대로(시도 횟수 증가 없음) |
| 404 (버킷/테이블 없음) | 설정 오류(잠정) | 401/403 과 같음 — 설정 실수로 전 이벤트가 DEAD_LETTER 되는 것을 막음 |
| 로컬 JPEG 없음/sha 불일치 | 재시도 불가 | DEAD_LETTER |
| `SYNC_MAX_ATTEMPTS` 도달 | 재시도 불가 | DEAD_LETTER |

- 백오프: `min(SYNC_RETRY_MAX_S, SYNC_RETRY_BASE_S × 2^attempt) × (0.5~1.0)`.
- IMAGE_UPLOADED 항목은 재시작 후 행 upsert 부터 이어간다(Storage 재요청 없음).
- 이미지는 파일 객체로 스트리밍 업로드한다(백로그를 메모리에 올리지 않음). 동시성은 1만 구현(`SYNC_CONCURRENCY>1` 은 경고 후 1).
- 경보는 현재 `ALERT` 로 시작하는 CRITICAL/ERROR 로그다. 외부 알림 연결은 통합 단계에서 D 와 결정.
- 보존 정책(`edge/event_store/retention.py`)은 10분마다 실행.

## 실행

저장소 루트가 `PYTHONPATH` 에 있어야 한다(`edge/` 는 네임스페이스 패키지). 컨테이너에는 `contracts/` 도 복사(`CONTRACTS_DIR`).

```bash
pip install -r edge/cloud_sync/requirements.txt
PYTHONPATH=. python -m edge.cloud_sync            # 상주: 수집 → 업로드 → 보존, 반복
PYTHONPATH=. python -m edge.cloud_sync once       # 한 번만 실행하고 결과 JSON 출력
PYTHONPATH=. python -m edge.cloud_sync status     # outbox 상태별 건수(Supabase 설정 불필요)
PYTHONPATH=. python -m edge.cloud_sync requeue <event_id>   # DEAD_LETTER 수동 확인 후 재시도
```

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `EVENT_DATA_ROOT` | `/data` | spool·images 루트 |
| `EVENT_STORE_DB_PATH` | `{EVENT_DATA_ROOT}/outbox.db` | |
| `SUPABASE_URL` | (필수) | `https://` 만(로컬 개발 `http://localhost` 허용) |
| `SUPABASE_SERVICE_KEY_FILE` | `/run/secrets/supabase_service_key` | 고권한 키 파일(0600/Docker secret). 로그·repr 에 출력 안 함 |
| `SUPABASE_SERVICE_KEY` | | (개발용 대안) 파일이 없을 때만 |
| `SUPABASE_EVENTS_TABLE` | `ppe_events` | |
| `SUPABASE_STORAGE_BUCKET` | `ppe-evidence` | |
| `SYNC_CONCURRENCY` | 1 | 현재 1만 지원 |
| `SYNC_RETRY_BASE_S` / `SYNC_RETRY_MAX_S` | 2 / 300 | |
| `SYNC_MAX_ATTEMPTS` | (비움 = 무제한) | |
| `SYNC_HTTP_TIMEOUT_S` | 15 | |
| `EVENT_LOCAL_RETENTION_DAYS` | 7 | |
| `EVENT_STORE_MAX_BYTES` | (비움 = 상한 없음) | |
| `SYNC_POLL_INTERVAL_S` | 2 | (추가) 할 일이 없을 때 대기 |
| `SYNC_AUTH_PAUSE_S` | 300 | (추가) 401/403/404 시 전체 중지 시간 |
| `SYNC_BATCH_SIZE` | 50 | (추가) 한 번에 꺼내는 outbox 항목 수 |
| `EVENT_STORE_SQLITE_SYNCHRONOUS` | `FULL` | (추가) 전원 차단 테스트(SYS-REL-04) 후 조정 |
| `CONTRACTS_DIR` | 저장소 `contracts/` | (추가) spool 스키마 검증용 |

(추가) 변수는 `infra/docker/.env.example`(D 소관)에 아직 없다. 반영 요청 필요.

## 구성

| 파일 | 내용 |
|---|---|
| `__main__.py` | 프로세스 루프, `once`/`status`/`requeue` |
| `config.py` | `SyncConfig.from_env()` |
| `record.py` | ppe-event → supabase-event-record |
| `supabase.py` | Storage/PostgREST 호출(urllib), 응답 분류, `Retry-After` |
| `worker.py` | `CloudSync.sync_once()` 상태 전이·백오프·일시 중지 |
| `tests/` | 대역 Supabase 로 순서·헤더·409·재시도·일시 중지·재시작·DEAD_LETTER·E2E(B SpoolPublisher → 업로드) |

## 검증

```bash
pip install -r edge/cloud_sync/requirements-dev.txt
python -m pytest edge/event_store/tests edge/cloud_sync/tests -q
```

- 개발 PC: 31건 통과. 업로드된 행 100% `supabase-event-record` 통과, 객체 sha256 일치.
- 실제 dev 프로젝트: anon 키로 HTTP 경로만 확인(테이블·버킷이 아직 없어 404 → 일시 중지로 분류됨). **service_role 키로 실제 업로드는 아직 안 했다.**
- 메모리(192m)·처리량·전원 차단: **미측정**.

## 남은 일

- dev 프로젝트에 `ppe_events`/`ppe-evidence` 생성(현재 `dashboard/seed/schema.sql`, 정식 마이그레이션은 `cloud/`) 후 실제 업로드 확인(I-4)
- `SYNC_CONCURRENCY>1`, 60분 백로그 메모리 피크(SYS-MEM-04), 강제 종료·WAN 단절 테스트(SYS-REL-01/02)
- 경보 전달 경로(로그 → ?) D 와 결정
