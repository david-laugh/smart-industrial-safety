# firmware/esp32-s3/ — 카메라 펌웨어

담당: **A (MCU / Camera)** · 상태: **전송 최소 구현** (Arduino, XIAO ESP32-S3 Sense)

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
├── frame_uploader/
│   ├── frame_uploader.ino     # 스케치 (외부 라이브러리 없음: 코어 내장 WiFi·esp_camera만 사용)
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
| 메타데이터 | `boot_id` = 부팅마다 `esp_random()` hex 8자리, `frame_seq` = 촬영한 프레임마다 0부터 +1, `mcu_uptime_ms` = 프레임 버퍼 timestamp(esp_timer), `trigger` = `PERIODIC`, `firmware_version` = `0.1.0` |
| 전송 | `metadata`(application/json) → `image`(image/jpeg, `frame.jpg`). JPEG 는 프레임 버퍼에서 소켓으로 바로 씀(복사 없음). `Connection: close` |
| 사람 감지 | `detectPerson()` 스텁(항상 false). `USE_EDGE_DETECTION=1` 이면 사람 있을 때만 전송, `trigger=EDGE_PERSON_DETECTED`, `edge_detection` 채움 |

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

# 사람 감지 경로까지 빌드
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi \
  --build-property "compiler.cpp.extra_flags=-DUSE_EDGE_DETECTION=1" firmware/esp32-s3/frame_uploader
```

업로드가 안 되면 BOOT 버튼을 누른 채 RESET 을 눌러 다운로드 모드로 들어간다.
Arduino IDE 는 `frame_uploader.ino` 를 열고 보드 `XIAO_ESP32S3`, `Tools > PSRAM > OPI PSRAM` 선택 후 업로드.

## 시리얼 로그 예시 (115200)

```text
[boot] frame_uploader fw=0.1.0 device=cam-line-a-01
[boot] boot_id=9f3c01ab edge_detection=0
[boot] camera ok heap=245112 psram=8121012
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

## 남은 일

- 실기 검증(카메라·Wi-Fi·SNTP·장시간)
- 413 시 품질/해상도 자동 하향, 5xx 지수 백오프(protocols.md) — 현재는 1회 재시도만
- HTTP keep-alive, 토큰을 NVS 로 옮기기, Edge Impulse 모델 연결
