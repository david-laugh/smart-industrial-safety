# edge/ppe_decision/ — PPE 착용 판정 및 이벤트 생성

담당: **B (AI / Vision)** · 배포: `edge-core` (3-컨테이너 시 `inference`) · 상태: 구현 전

## 책임

- 프레임 단위 결과를 사람 단위로 묶어 다중 프레임 평활화
- 위반 확정(`HELMET_MISSING`, `VEST_MISSING`), UNKNOWN 정책 적용
- 의미적 중복 억제(`dedup_key`, `dedup_window_s`)
- `event_id`(UUIDv7) **1회 생성** — 이후 어떤 단계에서도 재생성 금지
- 증거 JPEG와 이벤트 JSON을 spool에 원자적으로 게시(JPEG 먼저, JSON 나중)
- `occurred_at_utc` 결정(시간 규칙)

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | [`inference-result`](../../contracts/inference-result.schema.json) (+ 원본 JPEG 참조) |
| 출력 | [`ppe-event`](../../contracts/ppe-event.schema.json) → `/data/spool/incoming/{event_id}.json`, JPEG → `/data/images/{yyyy}/{mm}/{dd}/{event_id}.jpg` |

상세: [docs/protocols.md §4, §시간 규칙](../../docs/protocols.md)

## 정책 파라미터

`PPE_DECISION_WINDOW_FRAMES`, `PPE_DECISION_MIN_MISSING_FRAMES`, `PPE_UNKNOWN_IS_VIOLATION`, `PPE_DEDUP_WINDOW_S`, `PPE_DEDUP_GRID`, `TIME_MAX_DEVICE_SKEW_MS`, `PPE_POLICY_VERSION`

정책을 바꾸면 `PPE_POLICY_VERSION`을 올리고 이벤트의 `decision.policy_version`에 기록한다.
