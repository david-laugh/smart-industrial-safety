# cloud/ — Supabase 설계

담당: **C (Edge / Cloud)** · 상태: 설계만 존재 (마이그레이션 SQL 없음)

구현 단계에서 이 디렉터리에 Supabase 마이그레이션을 추가한다. 아래는 그 기준이 되는 설계다.

## 1. 테이블 `public.ppe_events` (설계)

행 형식의 정식 계약: [`supabase-event-record.schema.json`](../contracts/supabase-event-record.schema.json)

| 컬럼 | 타입(설계) | 제약 | 비고 |
|---|---|---|---|
| `event_id` | uuid | **PK** | 멱등성 기준 |
| `schema_version` | text | not null | |
| `device_id` | text | not null | |
| `occurred_at` | timestamptz | not null | 정렬 기준 |
| `occurred_at_source` | text | check in (`DEVICE_SNTP`, `GATEWAY_RECEIVED`) | |
| `gateway_received_at` | timestamptz | not null | |
| `decided_at` | timestamptz | not null | |
| `violation_count` | smallint | > 0 | |
| `violation_types` | text[] | not null | 필터용 |
| `storage_bucket` | text | not null | |
| `storage_path` | text | not null, unique | |
| `image_sha256` | text | not null | |
| `model_name` | text | not null | |
| `model_version` | text | not null | |
| `event_payload` | jsonb | not null | 원본 이벤트 |
| `ingested_at` | timestamptz | default now() | 서버 생성, 클라이언트가 보내지 않음 |

인덱스(설계): `(occurred_at desc)`, `(device_id, occurred_at desc)`, `violation_types`에 GIN.

## 2. Storage

| 항목 | 값 |
|---|---|
| 버킷 | `ppe-evidence` |
| 공개 여부 | **비공개** |
| 객체 경로 | `{device_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg` (occurred_at UTC) |
| 허용 MIME | `image/jpeg` |
| 크기 상한 | 1 MiB (계약과 동일) |
| 대시보드 접근 | `createSignedUrl` 짧은 만료(예: 수 분) |

## 3. 접근 제어 (RLS 설계)

| 주체 | ppe_events | Storage 객체 |
|---|---|---|
| anon (비로그인) | 접근 불가 | 접근 불가 |
| authenticated (대시보드 사용자) | select만 | Signed URL 발급(읽기)만 |
| cloud-sync | insert(upsert) | upload |

cloud-sync 자격증명 옵션:

| 옵션 | 장점 | 단점 |
|---|---|---|
| A. service_role 키를 cloud-sync에만 보관 (초기안) | 구현 단순 | RLS 우회 고권한 키가 현장 장치에 존재 → 유출 위험 |
| B. Edge Function 경유 + 장치별 토큰 | 장치 키 권한 최소화, 서버 측 검증 | 함수 구현·운영 필요 |
| C. 장치 전용 DB 역할/사용자 + 제한 정책 | 권한 최소화 | 토큰 갱신 관리 필요 |

초기 통합(M2~M3)은 A로 시작하되 개발용 프로젝트에서만 사용하고, 운영 전 B 또는 C로 전환을 검토한다.

## 4. 보존·개인정보

- 증거 이미지는 작업자가 식별될 수 있는 개인정보다. 보존 기간(일)과 삭제 절차를 현장 정책에 맞춰 정한다(TBD).
- 대시보드 접근 로그, 계정 관리 주체를 정한다(TBD).

## 5. 환경 분리

- `dev` 프로젝트: 통합·장애 테스트용
- `prod` 프로젝트: 현장 배포 이후
- 키와 URL은 저장소에 커밋하지 않는다.
