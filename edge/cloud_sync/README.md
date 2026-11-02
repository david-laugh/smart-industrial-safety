# edge/cloud_sync/ — Supabase 동기화

담당: **C (Edge / Cloud)** · 배포: `cloud-sync` (mem_limit 192m) · 상태: 구현 전

## 책임

- outbox `PENDING` 이벤트를 `occurred_at` 오래된 순으로 업로드
- 1단계 Storage 업로드(비공개 버킷, `x-upsert: false`, 409 시 해시 확인) → 2단계 `ppe_events` upsert(`on_conflict=event_id`, ignore-duplicates)
- 재시도 분류, 지수 백오프 + 지터, `Retry-After` 준수
- 401/403 시 업로드 일시 중지 + 경보
- `event_id`를 절대 재생성하지 않음

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | outbox 레코드 + 로컬 JPEG |
| 출력 | [`supabase-event-record`](../../contracts/supabase-event-record.schema.json), Storage 객체 `{device_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg` |

상세: [docs/protocols.md §5](../../docs/protocols.md)

## 설계 메모

- 메모리 상한이 작으므로(192m) 이미지 업로드는 스트리밍/파일 단위로 하고, 백로그 전체를 메모리에 올리지 않는다.
- 동시성 `SYNC_CONCURRENCY=1`부터 측정.
- 고권한 키는 이 컨테이너에만 존재. 파일/secret으로 주입, 로그에 출력 금지.

## 관련 환경변수

`SUPABASE_*`, `SYNC_*` — [infra/docker/.env.example](../../infra/docker/.env.example)
