# edge/gateway/ — 프레임 수신 게이트웨이

담당: **C (Edge / Cloud)** · 배포: `edge-core` (3-컨테이너 시 `gateway`) · 상태: 구현 전

## 책임

- `POST /api/v1/frames` multipart 수신, `GET /healthz`
- 장치 인증(Bearer 토큰 ↔ `device_id`)
- 메타데이터 스키마 검증, JPEG 크기·마커 검증, 크기 상한
- `frame_id`(UUIDv7), `gateway_received_at_utc`, `gateway_clock_synced` 부여
- 프레임 선별 큐: 장치별 latest-wins, 프레임 수·바이트 상한, 드롭 카운트
- 백프레셔: `429` + `retry_after_ms`

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | [`esp32-frame-metadata`](../../contracts/esp32-frame-metadata.schema.json) + `image` 파트 |
| 출력(ESP32) | [`gateway-response`](../../contracts/gateway-response.schema.json) |
| 출력(Inference) | [`edge-frame`](../../contracts/edge-frame.schema.json) |

상세: [docs/protocols.md §1, §2](../../docs/protocols.md)

## 설계 메모

- 프레임은 디스크에 쓰지 않는다(SD카드 쓰기 최소화). 메모리 큐 상한이 곧 메모리 상한.
- HTTP 워커는 1개부터 시작(워커 수 = 메모리 증가).
- 노출 메트릭(구현 단계): 수락/거부 수(코드별), 드롭 수, 큐 길이(프레임/바이트), 장치별 입력 FPS.

## 관련 환경변수

`GATEWAY_*`, `INFERENCE_SOCKET_PATH` — [infra/docker/.env.example](../../infra/docker/.env.example)
