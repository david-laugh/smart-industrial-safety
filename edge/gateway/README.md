# edge/gateway/ — 프레임 수신 게이트웨이

담당: **C (Edge / Cloud)** · 배포: `edge-core` (3-컨테이너 시 `gateway`) · 상태: **최소 구현** (2-컨테이너 IN_MEMORY 모드)

## 책임

- `POST /api/v1/frames` multipart 수신, `GET /healthz`
- 장치 인증(Bearer 토큰 ↔ `device_id`)
- 메타데이터 스키마 검증, JPEG 크기·마커 검증, 크기 상한
- `frame_id`(UUIDv7), `gateway_received_at_utc`, `gateway_clock_synced` 부여
- 프레임 선별 큐: 장치별 latest-wins, 프레임 수·바이트 상한, 드롭 카운트
- 백프레셔: `429` + `retry_after_ms`
- 추론 워커 스레드 1개: 큐에서 꺼내 `PPEPipeline.process(edge_frame, jpeg)` 호출

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | [`esp32-frame-metadata`](../../contracts/esp32-frame-metadata.schema.json) + `image` 파트 |
| 출력(ESP32) | [`gateway-response`](../../contracts/gateway-response.schema.json) |
| 출력(Inference) | [`edge-frame`](../../contracts/edge-frame.schema.json) (`image.location.kind=IN_MEMORY`) |

상세: [docs/protocols.md §1, §2](../../docs/protocols.md)

## 실행

저장소 루트에서(루트가 `PYTHONPATH` 에 있어야 함):

```bash
pip install -r edge/gateway/requirements.txt -r edge/inference/requirements.txt
export GATEWAY_DEVICE_TOKENS_FILE=/run/secrets/device_tokens
python -m edge.gateway
```

- uvicorn 워커 1개. 설정·계약 스키마·모델(`PPEPipeline.from_env()`)을 서버 시작 **전에** 모두 로드하며,
  하나라도 실패하면 종료 코드 2로 즉시 끝난다.
- 추론/판정 설정(`INFERENCE_*`, `PPE_*`, `EVENT_DATA_ROOT`)은 [ppe_decision/HANDOFF.md §6](../ppe_decision/HANDOFF.md) 참고.
- 계약 스키마는 `CONTRACTS_DIR`(기본: 저장소 `contracts/`)에서 읽는다. 컨테이너 이미지에 `contracts/` 를 함께 복사할 것.

### 토큰 파일

한 줄에 `device_id=token`. 빈 줄과 `#` 주석은 무시. 형식 오류, 중복 `device_id`, 여러 장치가 같은 토큰을 쓰는 경우,
장치가 하나도 없는 경우 시작 시 실패한다. **토큰 값은 커밋하지 않는다.**

```text
# /run/secrets/device_tokens
cam-line-a-01=<random-token>
cam-line-b-01=<random-token>
```

## 환경변수

| 변수 | 기본값 | 검증 |
|---|---|---|
| `GATEWAY_DEVICE_TOKENS_FILE` | (필수) | 읽을 수 있고 장치 1개 이상 |
| `GATEWAY_BIND_ADDR` | `0.0.0.0` | |
| `GATEWAY_PORT` | `8080` | 1–65535 |
| `GATEWAY_MAX_REQUEST_BYTES` | `1179648` | ≥ `GATEWAY_MAX_IMAGE_BYTES` |
| `GATEWAY_MAX_IMAGE_BYTES` | `1048576` | 1 – 1048576 (스키마 `byte_length` 상한) |
| `GATEWAY_QUEUE_MAX_FRAMES` | `4` | ≥ 1 |
| `GATEWAY_QUEUE_MAX_BYTES` | `4194304` | ≥ `GATEWAY_MAX_IMAGE_BYTES` |
| `GATEWAY_RETRY_AFTER_MS` | `1000` | ≥ 0 |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `CONTRACTS_DIR` | 저장소 `contracts/` | 필요한 스키마가 있어야 함 |

빈 값은 미설정으로 보고 기본값을 쓴다. `GATEWAY_PER_DEVICE_MAX_FPS`, `INFERENCE_SOCKET_PATH` 는 아직 쓰지 않는다.

## 검증 순서와 응답

앞 단계에서 실패하면 뒤 단계는 보지 않는다. 모든 응답 본문은 `gateway-response` 스키마를 따른다.

| # | 검사 | HTTP | `error.code` |
|---|---|---|---|
| 1 | Bearer 토큰이 등록된 장치의 것인지 | 401 | `UNAUTHORIZED` |
| 2 | 요청 크기 > `GATEWAY_MAX_REQUEST_BYTES` (Content-Length, 없으면 읽으면서) | 413 | `PAYLOAD_TOO_LARGE` |
| 3 | 요청이 `multipart/form-data` 가 아님 / multipart 형식 오류 | 415 / 400 | `UNSUPPORTED_MEDIA_TYPE` / `INVALID_METADATA` |
| 4 | `metadata.device_id` ≠ 토큰의 장치 | 401 | `UNAUTHORIZED` |
| 5 | `image` 파트 > `GATEWAY_MAX_IMAGE_BYTES` | 413 | `PAYLOAD_TOO_LARGE` |
| 6 | `image` 파트 Content-Type ≠ `image/jpeg` | 415 | `UNSUPPORTED_MEDIA_TYPE` |
| 7 | metadata 파트 없음 / JSON 아님 / 스키마 실패 / image 파트 없음 | 400 | `INVALID_METADATA` |
| 8 | image 크기 ≠ `byte_length`, SOI(`FFD8`)/EOI(`FFD9`) 불일치 | 400 | `IMAGE_METADATA_MISMATCH` |
| 9 | 선별 큐 상한 초과 | 429 | `QUEUE_FULL` (+ `retry_after_ms`) |
| — | 처리 중 예기치 못한 예외 | 500 | `INTERNAL_ERROR` |
| ✓ | 성공 (추론 큐에 들어감, 판정 완료 아님) | 202 | — |

## 프레임 선별 큐

- 메모리 전용. multipart 도 직접 메모리에서 파싱한다(Starlette `form()` 은 1 MiB 넘는 파일 파트를 디스크로 넘기므로 쓰지 않음).
- 장치별 대기 1장(latest-wins). 교체된 이전 프레임은 드롭되고 `dropped_since_last_selected` 가 증가하며,
  새 프레임은 이전 프레임의 대기 순서를 물려받는다.
- 전체 상한: 대기 프레임 수, JPEG 바이트 합. 넘으면 새 프레임을 `429` 로 거부(대기 중인 프레임은 그대로).
- 꺼내는 순서: `edge_detection.person_detected=true` 우선, 그다음 먼저 들어온 순. 사람 프레임이 와도 다른 장치의
  대기 프레임을 밀어내지는 않는다.
- `selection.reason`: `EDGE_PERSON_HINT`(사람 감지) > `MANUAL`(trigger) > `LATEST_FRAME`. `SAMPLING_INTERVAL` 은 미사용.
- `selection.dropped_since_last_selected` 는 워커가 꺼낼 때 확정한다. 429 로 거부된 프레임은 세지 않는다.

## 추론 워커

스레드 1개가 큐에서 꺼내 `PPEPipeline.process()` 를 순차 호출한다. `InvalidImageError`(JPEG 디코드 실패)는 경고,
`OSError`(spool 쓰기 실패)와 그 밖의 예외는 스택 트레이스를 로그로 남기고 다음 프레임으로 넘어간다. 워커는 죽지 않는다.

## `gateway_clock_synced`

Linux 에서 `adjtimex(2)` 읽기 전용 호출로 커널 동기화 상태를 본다(`TIME_ERROR` = 미동기화, 5초 캐시).
chrony / systemd-timesyncd 가 동기화하면 true. Linux 가 아니거나 확인에 실패하면 false.

## `GET /healthz`

정상이면 200, 워커 스레드가 죽었으면 503. 본문(계약 범위 밖, 바뀔 수 있음):

```json
{"status": "ok",
 "queue": {"frames": 0, "bytes": 0, "max_frames": 4, "max_bytes": 4194304},
 "counters": {"accepted": 3, "rejected": 1, "rejected_by_code": {"UNAUTHORIZED": 1},
              "dropped": 0, "processed": 3, "failed": 0, "events": 2},
 "worker_alive": true, "clock_synced": false}
```

## curl 예시

```bash
# metadata.json 의 image.byte_length 는 frame.jpg 크기와 같아야 한다
curl -sS -H "Authorization: Bearer $TOKEN" \
  -F "metadata=<metadata.json;type=application/json" \
  -F "image=@frame.jpg;type=image/jpeg" \
  http://<pi-address>:8080/api/v1/frames
# → 202 {"schema_version":"1.0.0","accepted":true,"frame_id":"01a1…","gateway_received_at_utc":"…Z",
#        "queue_depth":1,"retry_after_ms":null,"error":null}

curl -sS http://<pi-address>:8080/healthz
```

예시 메타데이터: [`contracts/examples/esp32-frame-metadata.example.json`](../../contracts/examples/esp32-frame-metadata.example.json)
(`byte_length` 를 실제 JPEG 크기로 바꿔서 사용).

## 테스트

```bash
pip install -r edge/gateway/requirements-dev.txt
python -m pytest edge/gateway/tests -q
```

`PPEPipeline` 은 가짜로 주입하므로 ONNX 모델·onnxruntime 없이 동작한다.

## 범위 밖 / 남은 일

- 3-컨테이너 UDS 모드(`INFERENCE_SOCKET_PATH`), 메트릭 엔드포인트, 장치별 FPS 제한(`RATE_LIMITED`), Dockerfile(D)
- Pi 5 에서 메모리·지연 측정

## 설계 메모

- 프레임은 디스크에 쓰지 않는다(SD카드 쓰기 최소화). 메모리 큐 상한이 곧 메모리 상한.
- HTTP 워커는 1개(워커 수 = 메모리 증가).
