# 프로토콜 및 데이터 인터페이스

계약의 정식 정의는 `contracts/*.schema.json`이다. 이 문서는 전송 방식, 동작 규칙, 오류 처리 등 스키마로 표현하기 어려운 부분을 정의한다. 문서와 스키마가 충돌하면 **스키마가 우선**이며, 충돌은 버그로 보고한다.

## 인터페이스 한눈에 보기

| # | 구간 | 전송 | 스키마 |
|---|---|---|---|
| 1 | ESP32-S3 → Gateway | HTTP/1.1 `multipart/form-data` | `esp32-frame-metadata`, 응답 `gateway-response` |
| 2 | Gateway → Inference | 프로세스 내부 객체 (3-컨테이너: UDS 길이 접두 프레임) | `edge-frame` |
| 3 | Inference → PPE Decision | 프로세스 내부 객체 | `inference-result` |
| 4 | PPE Decision → Cloud Sync | 공유 볼륨 spool (JSON 파일 + JPEG 파일) | `ppe-event` |
| 5 | Cloud Sync → Supabase | HTTPS: Storage API + PostgREST | `supabase-event-record` |

---

## 1. ESP32-S3 → Gateway

### 요청

```
POST /api/v1/frames HTTP/1.1
Host: <pi-address>:<GATEWAY_PORT>
Authorization: Bearer <device-token>
Content-Type: multipart/form-data; boundary=<boundary>

--<boundary>
Content-Disposition: form-data; name="metadata"
Content-Type: application/json

{ ...esp32-frame-metadata.schema.json 인스턴스... }
--<boundary>
Content-Disposition: form-data; name="image"; filename="frame.jpg"
Content-Type: image/jpeg

<JPEG 바이너리>
--<boundary>--
```

규칙:
- 파트 순서는 `metadata` → `image`. Gateway는 메타데이터를 먼저 검증하고 실패하면 이미지 본문을 버린다.
- 이미지를 JSON 안에 Base64로 넣지 않는다(스키마가 정의되지 않은 필드를 거부).
- `image` 파트 크기는 `metadata.image.byte_length`와 정확히 같아야 한다.
- 요청 전체 크기 상한: `GATEWAY_MAX_REQUEST_BYTES`, 이미지 상한: `GATEWAY_MAX_IMAGE_BYTES`(기본 1 MiB, 스키마 상한과 동일).
- Gateway 검증 항목: 토큰 ↔ `device_id` 일치, 스키마, JPEG SOI(`FFD8`)/EOI(`FFD9`) 마커, 선언 크기와 실제 디코드 크기 일치(디코드는 추론 단계에서 수행해도 됨).

### 응답

| HTTP | `accepted` | `error.code` | ESP32 동작 |
|---|---|---|---|
| 202 | true | — | 다음 프레임 진행 |
| 400 | false | `INVALID_METADATA`, `IMAGE_METADATA_MISMATCH` | 재전송하지 않음, 로그 기록(펌웨어 버그) |
| 401 | false | `UNAUTHORIZED` | 재전송하지 않음, 설정 오류 표시 |
| 413 | false | `PAYLOAD_TOO_LARGE` | 품질/해상도 낮춰 다음 프레임 |
| 415 | false | `UNSUPPORTED_MEDIA_TYPE` | 재전송하지 않음 |
| 429 | false | `QUEUE_FULL`, `RATE_LIMITED` | `retry_after_ms` 이상 대기 |
| 500/503 | false | `INTERNAL_ERROR` | 지수 백오프(최대 `retry_after_ms` 또는 펌웨어 상한) |

- 프레임은 시간이 지나면 가치가 떨어지므로 **ESP32는 실패한 프레임을 큐에 쌓아 재전송하지 않는다.** 네트워크 오류 시 최대 1회만 즉시 재시도하고 이후에는 새 프레임을 보낸다.
- 202는 "추론 큐에 들어감"을 의미하며 판정 완료를 의미하지 않는다.

### 헬스체크

`GET /healthz` → 200 (본문 형식은 구현 단계에서 정의, 계약 범위 밖).

---

## 2. Gateway → Inference (`edge-frame`)

- 기본(2-컨테이너): 같은 프로세스 안에서 객체로 전달. JPEG 바이트는 `image.location.kind=IN_MEMORY`.
- 3-컨테이너: 공유 볼륨의 Unix domain socket. 메시지 = `[4바이트 big-endian JSON 길이][edge-frame JSON][4바이트 JPEG 길이][JPEG 바이트]`. `location.kind=UNIX_SOCKET_STREAM`.
- `frame_id`는 Gateway가 생성(UUIDv7 권장). `capture_ts_utc`는 ESP32 값을 수정 없이 전달한다.
- 프레임 선별 정책(초기안, 파라미터화):
  1. 장치별로 대기 프레임은 최대 1장(latest-wins). 새 프레임이 오면 대기 중인 이전 프레임을 드롭하고 `dropped_since_last_selected` 증가.
  2. `edge_detection.person_detected=true` 프레임은 같은 시점의 다른 장치 프레임보다 우선.
  3. 전체 큐는 `GATEWAY_QUEUE_MAX_FRAMES`, `GATEWAY_QUEUE_MAX_BYTES` 이내.

## 3. Inference → PPE Decision (`inference-result`)

- 프레임 1장당 결과 1건. 사람이 없으면 `persons: []`(Decision은 이것도 "해당 장치 화면에 사람 없음" 신호로 사용).
- 사람-PPE 연관: PPE 검출 박스와 사람 박스의 포함/IoU 관계로 결정한다. 안전모는 사람 박스 상단 영역, 조끼는 몸통 영역 기준(구체 비율은 B가 정의하고 모델 카드에 기록).
- 상태 정의:
  - `WORN`: 해당 PPE가 그 사람에게 연관되어 임계값 이상으로 검출됨
  - `MISSING`: 해당 신체 부위가 충분히 보이는데 PPE가 검출되지 않음 또는 "미착용" 클래스가 임계값 이상
  - `UNKNOWN`: 부위 가림, 프레임 밖, 사람 신뢰도 낮음, 너무 작은 박스 등 판정 불가
- `timing`은 반드시 실측값을 기록한다(0 고정 금지 — 예시 파일의 0은 자리표시자).

## 4. PPE Decision → Cloud Sync (`ppe-event`)

### 판정 정책(초기안)

- 같은 사람(추적 ID 또는 위치 근접)에 대해 최근 `PPE_DECISION_WINDOW_FRAMES`장 중 `PPE_DECISION_MIN_MISSING_FRAMES`장 이상 MISSING이면 위반 확정.
- UNKNOWN은 MISSING으로 세지 않는다(`PPE_UNKNOWN_IS_VIOLATION=false`).
- 위반 유형: `HELMET_MISSING`, `VEST_MISSING`. 한 사람에게 둘 다 있을 수 있다.

### 중복 방지 (두 계층)

| 계층 | 위치 | 기준 | 목적 |
|---|---|---|---|
| 의미적 중복 억제 | PPE Decision | `dedup_key`가 같으면 `dedup_window_s` 안에서 새 이벤트를 만들지 않음 | 같은 사람·같은 위반의 반복 기록 방지 |
| 전송 멱등성 | Cloud Sync / Supabase | `event_id` 기본키 | 재시도·재시작으로 인한 중복 행/객체 방지 |

`dedup_key` 형식: `{device_id}|{track_id 또는 pos:r{행}c{열}}|{정렬된 violation_types를 ,로 연결}|{window_start_epoch_s}`
- 추적기가 없으면 bbox 중심을 격자(`PPE_DEDUP_GRID`)로 양자화한 `pos:` 값을 쓴다.

### spool 게시 절차

1. `event_id` 생성(UUIDv7).
2. 증거 JPEG를 `/data/spool/tmp/`에 쓰고 fsync 후 `/data/images/{yyyy}/{mm}/{dd}/{event_id}.jpg`로 rename.
3. 이벤트 JSON을 `/data/spool/tmp/`에 쓰고 fsync 후 `/data/spool/incoming/{event_id}.json`으로 rename.
4. `image.image_path`는 `EVENT_DATA_ROOT`(`/data`) 기준 상대 경로(`images/…`).

cloud-sync는 `incoming/`의 JSON을 스키마 검증 후 SQLite outbox에 `INSERT OR IGNORE`(PK=`event_id`)하고, 커밋 후 JSON을 `processed/`로 옮긴다. 스키마 실패 파일은 `rejected/`로 옮기고 로그를 남긴다.

## 5. Cloud Sync → Supabase

### Outbox 상태 머신

```
PENDING ──(Storage 업로드 성공 또는 409+해시 일치)──▶ IMAGE_UPLOADED ──(행 upsert 성공)──▶ SYNCED
   │                                                     │
   └──(재시도 가능 오류)──▶ PENDING (next_attempt_at 갱신) ◀┘
   └──(재시도 불가 오류 또는 최대 시도 초과)──▶ DEAD_LETTER (수동 확인)
```

### 업로드 순서

1. **Storage**: 버킷 `SUPABASE_STORAGE_BUCKET`(비공개), 경로 `{device_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg`(occurred_at UTC 날짜), `x-upsert: false`.
   - 이미 존재(409)하면 객체 메타데이터/해시를 확인해 같으면 성공으로 간주.
2. **Database**: `POST /rest/v1/ppe_events?on_conflict=event_id`, `Prefer: resolution=ignore-duplicates,return=minimal`. 본문은 `supabase-event-record` 스키마.
3. 둘 다 성공하면 outbox `SYNCED`, 로컬 이미지 보존 정책 적용.

### 재시도 규칙

| 오류 | 분류 | 동작 |
|---|---|---|
| 네트워크 오류, 타임아웃, DNS 실패 | 재시도 | 지수 백오프 |
| 408, 429, 500, 502, 503, 504 | 재시도 | `Retry-After` 우선, 없으면 지수 백오프 |
| 400, 422 (본문 오류) | 재시도 불가 | DEAD_LETTER |
| 401, 403 | 설정 오류 | 업로드 전체 일시 중지 + 경보, 이벤트는 PENDING 유지 |

- 백오프: `min(SYNC_RETRY_MAX_S, SYNC_RETRY_BASE_S × 2^attempt) × (0.5~1.0 지터)`.
- 업로드는 `occurred_at` 오래된 순, 동시성 `SYNC_CONCURRENCY`(초기 1).
- `event_id`는 어떤 경우에도 재생성하지 않는다.

### 로컬 보존

- `SYNCED` 이미지는 `EVENT_LOCAL_RETENTION_DAYS` 후 삭제.
- 디스크 사용량이 `EVENT_STORE_MAX_BYTES`를 넘으면 `SYNCED` 오래된 것부터 삭제. 그래도 넘으면 새 이벤트 수집을 계속하되 경보를 낸다(미전송 이벤트는 자동 삭제하지 않는 것이 기본).

---

## 시간 규칙

| 필드 | 시계 | 의미 |
|---|---|---|
| `mcu_uptime_ms` | ESP32 단조 시계 | 부팅 후 경과 ms. 벽시계 아님. `boot_id`와 함께만 의미 있음 |
| `time_sync.capture_ts_utc` | ESP32 SNTP | 동기화된 경우에만 값, 아니면 null |
| `gateway_received_at_utc` | Raspberry Pi | multipart 수신 완료 시각 |
| `gateway_clock_synced` | Raspberry Pi | 수신 시점 NTP 동기화 여부(chrony/timesyncd 상태) |
| `decided_at_utc` | Raspberry Pi | 위반 확정 시각 |
| `occurred_at_utc` | 규칙으로 결정 | 대시보드 정렬 기준 |

`occurred_at_utc` 결정 규칙:
1. `capture_ts_utc`가 있고 `|capture_ts_utc − gateway_received_at_utc| ≤ TIME_MAX_DEVICE_SKEW_MS`이면 `capture_ts_utc`, `occurred_at_source=DEVICE_SNTP`.
2. 아니면 `gateway_received_at_utc`, `occurred_at_source=GATEWAY_RECEIVED`.

지연 측정(벤치마크)은 Pi 시계만 쓰는 구간(수신 → 판정)과 장치 시계가 포함된 구간(촬영 → 판정)을 구분해 보고한다.

## ID 규칙

| ID | 생성 위치 | 형식 | 불변 범위 |
|---|---|---|---|
| `boot_id` | ESP32 부팅 시 | hex 8자리 | 부팅 1회 |
| `frame_seq` | ESP32 | uint32 | 부팅 내 단조 증가 |
| `frame_id` | Gateway | UUID(v7 권장) | 프레임 수명 |
| `event_id` | PPE Decision | UUID(v7 권장) | **영구** — 저장, 재시도, 재업로드, Supabase PK |
