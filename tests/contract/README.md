# tests/contract/ — 계약 테스트 계획

담당: **D** · 상태: 계획 (코드 없음)

## 목적

생산자 출력이 `contracts/` JSON Schema(draft 2020-12)를 100% 만족하는지(CON-01~06) 자동으로 확인한다.

## 테스트 항목 (구현 예정)

| ID | 대상 | 내용 |
|---|---|---|
| CT-01 | 스키마 자체 | 메타스키마 검증, `$ref` 해석, `$id`↔파일명 일치 |
| CT-02 | `contracts/examples/` | 모든 예시가 대응 스키마 통과 |
| CT-03 | 부정 케이스 | Base64 이미지 필드, 비 UTC 시각, 잘못된 status, 위반 0건, 절대/상위 경로, 거부 응답의 frame_id, SNTP 동기화인데 capture_ts 없음 → 거부 |
| CT-04 | 실제 출력 샘플 | 각 역할이 제출한 샘플(펌웨어 로그 덤프, Gateway/추론/판정/동기화 출력) 통과 |
| CT-05 | 교차 일관성 | 같은 이벤트의 `ppe-event`와 `supabase-event-record.event_payload` 일치, `violation_types` 합집합 일치, `storage_path` 날짜 = `occurred_at` UTC 날짜 |
| CT-06 | event_id 불변 | 재시도 시나리오 로그에서 같은 이벤트의 `event_id` 동일 |

## 도구 선정 기준 (D 결정)

- draft 2020-12 지원, `$ref` 상대 경로 해석, `format`(uuid, date-time) 검증 옵션
- CI(개발 PC)와 Pi 양쪽에서 실행 가능

## 샘플 제출 위치 (구현 단계)

`tests/contract/samples/<역할>/<인터페이스>/*.json` — 개인정보·비밀 정보가 없는 샘플만.
