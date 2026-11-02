# 통합 보고서: XIAO ESP32S3 Sense → Raspberry Pi 5 Gateway → PPE 이벤트 (2026-11-02)

- 범위: 인터페이스 1~4 실기 연결 (ESP32 촬영·FOMO 사람 감지 → HTTP multipart → Gateway → PPEPipeline → spool)
- **범위 밖 / 미연결**: 인터페이스 5(cloud-sync → Supabase)와 대시보드 — Supabase dev 프로젝트에 `ppe_events`/`ppe-evidence` 미생성
- 관측 방법: Pi Gateway `GET /healthz` 3초 간격 폴링, Gateway 접근 로그, spool 이벤트 JSON, `/proc` (Pi 에서 읽기 전용)
- 증거 이미지(작업자 얼굴 포함)는 개인정보라 저장소에 넣지 않았다.

## 구성

| 구간 | 내용 |
|---|---|
| ESP32 | XIAO ESP32S3 Sense, `firmware/esp32-s3/frame_uploader` v0.2.0 (브랜치 `a/edge-fomo`), esp32 코어 2.0.17, PSRAM OPI |
| 사람 감지 | Edge Impulse `Person_detection_FOMO_inferencing` 1.0.5 (프로젝트 1154429, 64×64 RGB, int8), 임계값 0.5, 촬영 주기 500 ms |
| 전송 | VGA 640×480 JPEG q12, multipart(`metadata` → `image`), `Authorization: Bearer`, `device_id=xiao-cam-01` |
| 네트워크 | 같은 Wi-Fi LAN. XIAO 172.30.1.40 → Pi 172.30.1.100:8080 |
| Pi | Raspberry Pi 5 4GB, Debian 13 trixie, Python 3.13.5, native venv `~/ppe-gw` (master `6e7571c`), 팬 없음 |
| Gateway | `python -m edge.gateway`, `GATEWAY_BIND_ADDR=0.0.0.0`, `ORT_INTRA_OP_THREADS=2`, 큐 4프레임/4 MiB |
| 추론·판정 | `PPEPipeline` (`ppe_yolov8n_640.onnx` sha256 `846b747b…`), policy 0.0.0, dedup 30 s |

## 결과

### 수신 (Gateway 카운터, 15:23 KST 종료 시점)

| 지표 | 값 |
|---|---|
| POST `/api/v1/frames` (XIAO) | 978건, **전부 202** |
| accepted / processed | 978 / 977 (1건은 처리 중) |
| rejected / dropped / failed | 0 / 0 / 0 |
| Gateway 로그 경고·오류 | 0건 |
| 수신 속도 | 약 2 프레임/초 (seq 239→953, 362초 구간에서 촬영 714프레임) — 관측 동안 대부분 프레임에서 사람 감지 |

### 위반 이벤트 (XIAO, 7건)

| occurred_at (UTC) | frame_seq | 위반 | helmet | vest |
|---|---|---|---|---|
| 06:16:38.801 | 239 | HELMET_MISSING, VEST_MISSING | MISSING 0.85 | MISSING 0.59 |
| 06:16:41.323 | 244 | HELMET_MISSING | MISSING 0.78 | WORN 0.92 |
| 06:20:33.675 | 701 | VEST_MISSING | UNKNOWN | MISSING 0.53 |
| 06:21:06.307 | 766 | VEST_MISSING | UNKNOWN | MISSING 0.72 |
| 06:21:37.931 | 829 | VEST_MISSING | UNKNOWN | MISSING 0.62 |
| 06:22:11.067 | 895 | VEST_MISSING | UNKNOWN | MISSING 0.57 |
| 06:22:40.170 | 953 | HELMET_MISSING, VEST_MISSING | MISSING 0.73 | MISSING 0.57 |

- 7건 모두 `ppe-event` 스키마 통과, 증거 JPEG 존재, `boot_id` 동일(`061b2618`), `occurred_at_source=DEVICE_SNTP`, `gateway_clock_synced=true`
- 이벤트 간격 약 30초 = `PPE_DEDUP_WINDOW_S=30` 과 일치(같은 위치·같은 위반 재기록 억제 동작)

### 지연

| 구간 | min / median / max (ms) | 비고 |
|---|---|---|
| Gateway 수신 → 판정 (`decided − gateway_received`) | 201 / 202 / 203 | Pi 시계만 사용. B 벤치 스레드 2 결과(p50 201.9 ms)와 일치 |
| 촬영 → 판정 (`decided − occurred`) | 564 / 583 / 618 | ESP32 FOMO 추론 + Wi-Fi 전송 + ESP32·Pi SNTP 시계 차 포함 |

### Pi 자원 (종료 시점 1회 측정)

| 지표 | 값 |
|---|---|
| Gateway 프로세스 (추론 포함) RSS / 최대 RSS(VmHWM) | 177,635,328 B / 188,481,536 B |
| Gateway 프로세스 평균 CPU (실행 1,839초 동안) | 22.8 % (1코어 기준) |
| 호스트 사용량 (`MemTotal − MemAvailable`) | 394,035,200 B, swap 0 |
| 온도 / `get_throttled` | 64.2 °C / `0xe0000` (과거 발생 이력 비트. 오늘 오전 벤치마크 때 생긴 것, 현재 스로틀 아님) |

## 판정

| 항목 | 결과 |
|---|---|
| ESP32 FOMO 사람 감지 → 사람 있을 때만 전송 | **동작** (전송 978건 전부 202) |
| 계약 v1.0.0 메타데이터 + JPEG multipart | **통과** (Gateway 스키마 검증에서 거부 0) |
| Pi PPE 판정 → 위반 이벤트 생성 | **동작** (7건, 스키마 100% 통과) |
| 이벤트 중복 억제 | 30초 간격으로 동작 확인 |
| Supabase 업로드 / 대시보드 표시 | **미검증** (Supabase 테이블·버킷 없음) |

## 미검증 / 한계

- **FOMO 추론 시간과 감지 정확도(오탐·미탐)**: ESP32 시리얼 로그를 수집하지 않아 미측정. 사람 없는 장면에서 전송이 생략되는지도 Pi 쪽에서는 관측 불가(시리얼 로그 필요).
- **ESP32 장시간 메모리(P5)**: 약 30분 동작, 리셋 없이 `boot_id` 유지까지만 확인. heap/PSRAM 추이는 시리얼 `[stats]` 로그 필요.
- **판정 정확도**: 이벤트 3~6은 안전모 UNKNOWN(머리 영역 잘림 등 추정)이고 조끼 신뢰도 0.53~0.72. 실제 착용 상태와 대조하지 않았다.
- Pi 자원은 종료 시점 1회 값이며 시계열이 아니다. Docker, cloud-sync 미포함이라 시스템 전체 3.5 GB 판정 대상 아님.
- 단일 장치, 단일 장면, 약 30분.

## 다음 단계

1. C: Supabase dev 프로젝트에 `dashboard/seed/schema.sql` 적용 → Pi cloud-sync 기동 → 대시보드 `DASHBOARD_DATA_SOURCE=supabase`
2. A: 시리얼 로그로 FOMO 추론 시간·점수 분포, 사람 없음 장면 전송 생략, 60분 이상 heap/PSRAM 추이(P5)
3. B: 실제 XIAO 프레임으로 판정 임계값·최소 박스 비율 재조정(UNKNOWN 비율 확인)
4. D: Docker 배포에서 같은 흐름 재현, 호스트 메모리 시계열 측정
