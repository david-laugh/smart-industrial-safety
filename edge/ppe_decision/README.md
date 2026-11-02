# edge/ppe_decision/ — PPE 착용 판정 및 이벤트 생성

담당: **B (AI / Vision)** · 배포: `edge-core` (3-컨테이너 시 `inference`) · 상태: 구현됨(PC 검증)

> 통합 진입점·환경변수·spool 규칙: [`HANDOFF.md`](HANDOFF.md)

## 책임

- 프레임 단위 결과를 사람 단위(격자 셀)로 묶어 다중 프레임 평활화
- 위반 확정(`HELMET_MISSING`, `VEST_MISSING`), UNKNOWN 정책 적용
- 의미적 중복 억제(`dedup_key`, `dedup_window_s`)
- `event_id`(UUIDv7) **1회 생성** — 이후 어떤 단계에서도 재생성 금지
- 증거 JPEG와 이벤트 JSON을 spool에 원자적으로 게시(JPEG 먼저, JSON 나중)
- `occurred_at_utc` 결정(시간 규칙)

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | [`inference-result`](../../contracts/inference-result.schema.json) (+ edge-frame, 원본 JPEG) |
| 출력 | [`ppe-event`](../../contracts/ppe-event.schema.json) → `/data/spool/incoming/{event_id}.json`, JPEG → `/data/images/{yyyy}/{mm}/{dd}/{event_id}.jpg` |

```python
from edge.ppe_decision.pipeline import PPEPipeline
pipeline = PPEPipeline.from_env()
events = pipeline.process(edge_frame, jpeg)     # list[ppe-event], 위반자 1명당 1건, 이미 spool 게시됨

# 판정만 따로
from edge.ppe_decision.decision import PPEDecision
decision = PPEDecision()                         # DecisionConfig.from_env(), SpoolPublisher(EVENT_DATA_ROOT)
events = decision.update(edge_frame, inference_result, jpeg)
```

상세: [docs/protocols.md §4, §시간 규칙](../../docs/protocols.md)

## 판정 정책 (policy `0.0.0`)

1. **같은 사람**: 추적기 없음. bbox 중심을 `PPE_DEDUP_GRID`(가로x세로, 기본 4x4)로 양자화한 셀 `pos:r{행}c{열}`.
   격자 경계 떨림을 막기 위해, 해당 장치에서 추적 중인 셀의 마지막 중심과 `PPE_CELL_STICKY_DIST`(격자 단위, 기본 0.5) 이내면 그 셀을 재사용.
2. **평활화**: (장치, 셀)별 최근 `PPE_DECISION_WINDOW_FRAMES`(5) 프레임. 사람이 없는 프레임은 ABSENT(미착용으로 세지 않음).
   항목별 MISSING 수 ≥ `PPE_DECISION_MIN_MISSING_FRAMES`(3) **이고 현재 프레임도 MISSING** 이면 확정(증거 이미지에 위반이 보이도록).
   UNKNOWN 은 `PPE_UNKNOWN_IS_VIOLATION=true` 일 때만 MISSING 으로 센다. 같은 셀에 여러 명이면 MISSING > WORN > UNKNOWN.
3. **이벤트 단위**: 위반자 1명 = 이벤트 1건(자기 `event_id`, `dedup_key`, `violations` 1개, event_id 별 증거 JPEG).
4. **dedup**: `dedup_key = {device_id}|pos:r{r}c{c}|{정렬된 유형 ,연결}|{floor(t/W)*W}` (t = occurred_at epoch 초, W = `PPE_DEDUP_WINDOW_S`).
   같은 (장치, 셀, 유형)은 마지막 발행 후 W초 동안 재발행하지 않음(버킷 경계를 넘어도 억제). 유형 조합이 바뀌면 새 이벤트.
5. **결정적 동작**: 같은 입력 시퀀스 → 같은 판정. `event_id`(`id_fn`)와 `decided_at_utc`(`now_fn`)는 주입 가능.
6. **시간**: `capture_ts_utc` 가 있고 `|capture − gateway_received| ≤ TIME_MAX_DEVICE_SKEW_MS` → `DEVICE_SNTP`, 아니면 `GATEWAY_RECEIVED`.
7. **spool**: `spool/tmp/*.part` 에 쓰기+fsync → rename. JPEG 먼저, JSON 나중. 게시 실패 시 해당 이벤트는 dedup 에 기록하지 않아 다음 프레임에서 재발행.

정책을 바꾸면 `PPE_POLICY_VERSION` 을 올리고 이벤트의 `decision.policy_version` 에 기록한다.

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PPE_POLICY_VERSION` | `0.0.0` | |
| `PPE_DECISION_WINDOW_FRAMES` | 5 | |
| `PPE_DECISION_MIN_MISSING_FRAMES` | 3 | 1 ≤ 값 ≤ WINDOW |
| `PPE_UNKNOWN_IS_VIOLATION` | false | |
| `PPE_DEDUP_WINDOW_S` | 30 | 0~3600 |
| `PPE_DEDUP_GRID` | `4x4` | `{가로}x{세로}` |
| `PPE_CELL_STICKY_DIST` | 0.5 | (추가) 격자 단위, 0 = 비활성 |
| `TIME_MAX_DEVICE_SKEW_MS` | 2000 | |
| `EVENT_DATA_ROOT` | `/data` | 개발 시 `./_data` 등 |
| `PPE_EVENT_SCHEMA_VERSION` | `1.0.0` | (추가) |

## 구성

| 파일 | 내용 |
|---|---|
| `pipeline.py` | `PPEPipeline` 통합 진입점(infer → decision → spool) |
| `decision.py` | `PPEDecision` 평활화·확정·dedup·이벤트 조립, 시간 규칙, JPEG 크기 파싱 |
| `spool.py` | `SpoolPublisher` 원자적 게시 |
| `uuid7.py` | 표준 라이브러리 UUIDv7 |
| `config.py` | 환경변수 로드 |
| `tools/run_replay.py` | PC 단독 E2E 리플레이(영상 → edge-frame → 파이프라인 → spool, 스키마 100% 검증, timing p50/p95) |
| `tests/` | 평활화, UNKNOWN, dedup, 결정성, 시간 규칙, spool 순서·실패 복구, 위반자별 이벤트, 파이프라인 통합 |

이 모듈은 표준 라이브러리만 사용한다(`pipeline.py` 만 `edge.inference` 의존).

## 검증 결과 (PC, x86 참고값)

리플레이: 테스트 영상 ESP32 모사(640x480 JPEG q40), 2 fps, 4회 반복(40초, 80프레임)
→ 이벤트 4건(안전모 미착용 1명 + 조끼 미착용 1명 × 30초 창 2개, t=1.0s / 31.0s), 모든 edge-frame/inference-result/ppe-event 스키마 통과.

## 남은 이슈

- **추적기 부재**: 사람이 격자 셀을 크게 이동하면 평활화 창이 끊기고 새 셀에서 다시 시작(그동안 확정 지연, 30초 안 재발행 가능).
- **박스 기준·임계값 재조정 필요**: 추론 측 최소 박스(0.33)·임계값(0.5)은 테스트 영상 1개 기준.
- **ESP32 persons 필드**: 스키마 1.1.0 대기.
- 같은 프레임의 여러 이벤트는 같은 JPEG 내용을 event_id 별 파일로 중복 저장(디스크 사용량 증가, 계약상 의도된 동작).
