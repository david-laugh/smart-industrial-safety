# firmware/esp32-s3/ — 카메라 펌웨어

담당: **A (MCU / Camera)** · 상태: **FOMO 사람 감지 + 전송 실기 동작** (Arduino, XIAO ESP32-S3 Sense, 2026-11-02 Pi Gateway 202 수신·PPE 이벤트 생성 확인)

## 책임

- 카메라 초기화(PSRAM 프레임 버퍼), JPEG 촬영, 해상도·품질 설정
- (선택) Edge Impulse 사람 감지로 전송 트리거 결정 — **최종 판정은 Raspberry Pi**
- 메타데이터 생성: `boot_id`, `frame_seq`, `mcu_uptime_ms`, SNTP 상태, 이미지 정보
- `POST /api/v1/frames` multipart 업로드, 응답별 백오프
- Wi-Fi/SNTP 재연결, 워치독, 장치 토큰 보관

## 인터페이스

| 방향 | 계약 | 비고 |
|---|---|---|
| 출력 | [`esp32-frame-metadata.schema.json`](../../contracts/esp32-frame-metadata.schema.json) 1.0.0 + JPEG 파트 | Base64 금지 |
| 입력 | [`gateway-response.schema.json`](../../contracts/gateway-response.schema.json) | 429 → `retry_after_ms` 대기 |

상세 규칙: [docs/protocols.md §1](../../docs/protocols.md)

## 구조

```text
firmware/esp32-s3/
├── camera/camera_probe/
│   └── camera_probe.ino       # 카메라 단독 진단(Wi-Fi·AI 없음): init 결과·센서·JPEG·메모리
├── frame_uploader/
│   ├── frame_uploader.ino     # 스케치: 촬영 → Edge Impulse FOMO 사람 감지 → 사람 있을 때만 Pi 로 전송
│   ├── secrets.example.h      # 커밋됨. secrets.h 로 복사해서 사용
│   └── secrets.h              # .gitignore — 커밋 금지
├── tools/frame_sim.py         # 스케치와 같은 메타데이터·multipart 를 PC 에서 보내는 도구
└── tests/                     # 메타데이터 계약 테스트
```

## 동작 요약

| 항목 | 값 |
|---|---|
| 카메라 | XIAO ESP32-S3 Sense 핀맵, `FRAMESIZE_VGA`(640×480), JPEG quality 12, `fb_count` 2, PSRAM, `CAMERA_GRAB_LATEST` |
| 주기 | 500 ms (촬영 시작 기준. 전송이 500 ms 넘으면 바로 다음 프레임) |
| 시간 | `configTime(0,0,NTP_SERVER)`(UTC). 동기화 전에는 `synced=false, source="NONE", capture_ts_utc=null` |
| 메타데이터 | `boot_id` = 부팅마다 `esp_random()` hex 8자리, `frame_seq` = 촬영한 프레임마다 0부터 +1(감지 안 돼 건너뛴 프레임도 증가), `mcu_uptime_ms` = 프레임 버퍼 timestamp(esp_timer), `trigger` = `EDGE_PERSON_DETECTED`(감지 사용 시) / `PERIODIC`, `firmware_version` = `0.2.0` |
| 전송 | `metadata`(application/json) → `image`(image/jpeg, `frame.jpg`). JPEG 는 프레임 버퍼에서 소켓으로 바로 씀(복사 없음). `Connection: close` |
| 사람 감지 | `USE_EDGE_DETECTION=1`(기본): 아래 [사람 감지](#사람-감지-edge-impulse-fomo). 사람 없으면 전송 생략, 있으면 `edge_detection` 채움. `0` 이면 라이브러리 없이 주기 전송 |

## 사람 감지 (Edge Impulse FOMO)

| 항목 | 값 |
|---|---|
| 라이브러리 | `Person_detection_FOMO_inferencing` **1.0.5** (Edge Impulse Arduino library, 저장소에 포함하지 않음) |
| 모델 | Edge Impulse 프로젝트 **1154429** "Person detection FOMO"(공개 프로젝트 121370 사본), deploy v5, Studio 1.97.1 |
| 입력 | **64×64 RGB**, int8 양자화, 리사이즈 `EI_CLASSIFIER_RESIZE_FIT_LONGEST` |
| 출력 | FOMO(중심점 기반), 라벨 1개 `hum`, 최대 10개, 임계값 0.5 |
| `model_id` | `ei-1154429-v5-fomo64` (`EDGE_MODEL_ID`) |
| `person_score` | FOMO 검출 점수의 최댓값. `EDGE_PERSON_THRESHOLD`(기본 0.5) 이상이면 사람 있음 |

- **추론 이미지 = 전송 이미지**: 같은 프레임 버퍼의 JPEG 를 `fmt2rgb888` 로 디코드 → fit-longest 로 64×64 축소 → `run_classifier`. 사람이 있으면 그 JPEG 를 그대로 보낸다.
- FOMO 박스는 셀 단위 중심점이라 **사람 크기가 아니다**. 그래서 bbox 는 보내지 않고(계약 1.0.0 그대로), 사람 박스와 PPE 는 Pi 가 다시 검출한다.
- 디코드 버퍼(640×480×3 = 921,600 B)와 모델 입력 버퍼(64×64×3)는 부팅 시 PSRAM 에 1회 할당하고 재사용한다.
- 촬영 주기 `CAPTURE_INTERVAL_MS_CFG`(기본 500 ms, B 권장 2 fps)로 전송 빈도를 제한한다.

### 라이브러리 설치

1. Edge Impulse Studio → 프로젝트 1154429 → **Deployment** → *Arduino library* → Build (Quantized int8). 파일 예: `ei-person-detection-fomo-arduino-1.0.5-....zip`
2. Arduino IDE → **스케치 → 라이브러리 포함하기 → .ZIP 라이브러리 추가...** → 받은 zip 선택
   (arduino-cli: `arduino-cli lib install --zip-path <zip>`, 설정에서 `library.enable_unsafe_install: true` 필요)
3. 확인: `라이브러리 관리` 또는 `arduino-cli lib list` 에 `Person_detection_FOMO_inferencing 1.0.5`
4. zip 과 라이브러리 폴더는 저장소에 커밋하지 않는다. 첫 컴파일은 SDK 때문에 10분 이상 걸릴 수 있다.

### 응답 처리

| HTTP | 동작 |
|---|---|
| 202 | 다음 프레임 |
| 429 | 응답 `retry_after_ms` 만큼 대기(없으면 1000 ms, 상한 60 s) |
| 400 · 401 · 415 | 시리얼 로그만, 재전송 안 함 |
| 413 | 시리얼 로그(품질/해상도 낮출 것) |
| 5xx · 네트워크 오류 | 1회 즉시 재시도 후 다음 프레임 |

실패한 프레임은 큐에 쌓지 않는다.

## 보드 설정

- Arduino IDE 2.x 또는 arduino-cli, **esp32 코어 2.0.17**에서 확인 (3.x 는 미확인)
- 보드: `XIAO_ESP32S3` (FQBN `esp32:esp32:XIAO_ESP32S3`)
- **PSRAM: OPI PSRAM** (`PSRAM=opi`) — 꺼져 있으면 부팅 시 `PSRAM not found` 후 재시작
- 나머지 옵션은 기본값

## secrets 작성

```bash
cp firmware/esp32-s3/frame_uploader/secrets.example.h firmware/esp32-s3/frame_uploader/secrets.h
```

| 매크로 | 내용 |
|---|---|
| `WIFI_SSID`, `WIFI_PASSWORD` | 2.4 GHz Wi-Fi |
| `GATEWAY_HOST` | Pi 주소(IP/호스트명, `http://` 없이) |
| `GATEWAY_PORT` | `8080` |
| `DEVICE_ID` | Gateway 토큰 파일의 `device_id` (소문자·숫자·하이픈) |
| `DEVICE_TOKEN` | 같은 줄의 토큰 |
| `NTP_SERVER` | (선택) 기본 `pool.ntp.org`. 인터넷이 없으면 Pi 주소 |

`secrets.h` 가 없으면 컴파일 오류로 알려준다.

## 빌드·업로드

```bash
# 컴파일
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi firmware/esp32-s3/frame_uploader

# 업로드 (포트는 arduino-cli board list 로 확인)
arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi -p COM5 firmware/esp32-s3/frame_uploader
arduino-cli monitor -p COM5 -c baudrate=115200

# 사람 감지 없이(라이브러리 불필요) 주기 전송으로 빌드
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi \
  --build-property "compiler.cpp.extra_flags=-DUSE_EDGE_DETECTION=0" firmware/esp32-s3/frame_uploader
```

기본(감지 사용) 빌드 크기, 코어 2.0.17: 프로그램 946,485 B(28%), 전역 변수 53,472 B(16%).

## 카메라 진단 (`camera/camera_probe`)

카메라가 안 될 때 Wi-Fi·AI 를 빼고 먼저 확인한다. 같은 보드 설정(PSRAM OPI)으로 업로드하고 시리얼 115200.

| 로그 | 의미 |
|---|---|
| `[psram] found=1 size=…` | OPI PSRAM 인식 |
| `[camera] init OK … PID=… model=…` | 센서 인식(SCCB 정상) |
| `[camera] init FAILED err=0x105 (ESP_ERR_NOT_FOUND)` | 센서가 SCCB(SDA 40, SCL 39)에 응답 안 함 → B2B 커넥터·카메라 리본부터 확인 |
| `[capture] #n 640x480 jpeg=… SOI=1 EOI=1` | 촬영 정상 |
| `[mem] … heap_free / psram_free` | 10장마다. 값이 계속 줄면 프레임 버퍼 미반환 |

**0x105 이력 (2026-11-02)**: 인계 시 CameraWebServer 포함 `0x105` 보고 → Sense 확장 보드·카메라 연결 점검 후
`camera_probe` 로 640×480 JPEG 72장 연속 성공, 실패 0, heap/PSRAM 여유량 일정(heap_free 344,600 B, psram_free 8,324,599 B).
원인은 연결 상태로 추정(하드웨어 고장 아님). 센서 모델명 로그는 미수집.

업로드가 안 되면 BOOT 버튼을 누른 채 RESET 을 눌러 다운로드 모드로 들어간다.
Arduino IDE 는 `frame_uploader.ino` 를 열고 보드 `XIAO_ESP32S3`, `Tools > PSRAM > OPI PSRAM` 선택 후 업로드.

## 시리얼 로그 예시 (115200)

```text
[boot] frame_uploader fw=0.2.0 device=cam-line-a-01
[boot] boot_id=9f3c01ab edge_detection=1
[boot] edge model=ei-1154429-v5-fomo64 input=64x64 threshold=0.50 interval=500ms
[boot] camera ok heap=245112 psram=7121012
[frame] seq=0 no person (boxes=0 score=0.00 infer=…ms), skip
[edge] seq=1 person boxes=… score=… infer=…ms
[wifi] connected ip=192.168.0.31 rssi=-58
[time] SNTP synced
[frame] seq=0 jpeg=38211B http=202 ms=142
[frame] seq=1 jpeg=37984B http=202 ms=131
[frame] seq=2 jpeg=38102B http=429 ms=95 err=QUEUE_FULL
[http] 429 backoff 1000 ms
[frame] seq=3 http=-1 ms=3004 -> retry once
[frame] seq=3 jpeg=38050B http=202 ms=150 retried
[stats] sent=100 accepted=97 failed=3 heap=243880 psram=8058212 uptime=55s
```

`seq`, JPEG 크기, HTTP 코드(-1 = 네트워크 오류), POST 소요 ms. 100 프레임마다 힙/PSRAM 여유량.
(예시 값은 형식 설명용이며 실측치가 아니다.)

## 테스트 (PC)

```bash
pip install -r edge/gateway/requirements-dev.txt
python -m pytest firmware/esp32-s3/tests -q
```

스케치와 같은 규칙으로 만든 메타데이터(동기화/미동기화/사람 감지)를 계약 스키마로 검증하고, 같은 multipart 바이트를
Gateway 앱에 넣어 202 를 확인한다. 스케치 상수와 `tools/frame_sim.py` 가 어긋나면 실패한다.

로컬 Gateway 에 직접 보내기:

```bash
python firmware/esp32-s3/tools/frame_sim.py --host 127.0.0.1 --port 8080 \
  --device-id cam-line-a-01 --token <token> --jpeg frame.jpg --count 5 [--unsynced]
```

## 측정해서 보고할 것 (실기)

- 프레임당 JPEG 크기 분포, 촬영→전송 완료 시간, 실제 전송 FPS
- (선택) 사람 감지 지연과 정확도
- 힙/PSRAM 여유량, 장시간 리셋 여부

## 실기 확인 (2026-11-02, XIAO → Pi 5 Gateway, native)

- XIAO(172.30.1.40, `device_id=xiao-cam-01`) → Pi Gateway(`0.0.0.0:8080`, `ORT_INTRA_OP_THREADS=2`): 약 2 장/초,
  978건 전부 `202`, 거부·드롭·실패 0
- Pi 판정 위반 이벤트 7건(스키마 100% 통과, `occurred_at_source=DEVICE_SNTP`). 촬영→판정 median 583 ms(ESP32·Pi 시계 차 포함),
  Gateway 수신→판정 median 202 ms
- 상세: [tests/integration/results/20261102-xiao-pi-e2e/report.md](../../tests/integration/results/20261102-xiao-pi-e2e/report.md)
- 미측정: FOMO 추론 시간·감지 정확도, 장시간 메모리(P5), Supabase·대시보드 연결(C 쪽 dev 프로젝트 테이블 생성 대기)

## 남은 일

- 장시간 실행 메모리 안정성(P5), FOMO 추론 시간·오탐/미탐 측정
- 413 시 품질/해상도 자동 하향, 5xx 지수 백오프(protocols.md) — 현재는 1회 재시도만
- HTTP keep-alive, 토큰을 NVS 로 옮기기
