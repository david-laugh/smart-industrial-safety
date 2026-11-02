# firmware/esp32-s3/ — 카메라 펌웨어

담당: **A (MCU / Camera)** · 상태: 구현 전 (소스 없음)

## 책임

- 카메라 초기화(PSRAM 프레임 버퍼), JPEG 촬영, 해상도·품질 설정
- (선택) Edge Impulse 사람 감지로 전송 트리거 결정 — **최종 판정은 Raspberry Pi**
- 메타데이터 생성: `boot_id`, `frame_seq`, `mcu_uptime_ms`, SNTP 상태, 이미지 정보
- `POST /api/v1/frames` multipart 업로드, 응답별 백오프
- Wi-Fi/SNTP 재연결, 워치독, 장치 토큰 보관

## 인터페이스

| 방향 | 계약 | 비고 |
|---|---|---|
| 출력 | [`esp32-frame-metadata.schema.json`](../../contracts/esp32-frame-metadata.schema.json) + JPEG 파트 | Base64 금지 |
| 입력 | [`gateway-response.schema.json`](../../contracts/gateway-response.schema.json) | 429 → `retry_after_ms` 대기 |

상세 규칙: [docs/protocols.md §1](../../docs/protocols.md)

## 설계 결정 필요 (A)

| 항목 | 후보/메모 |
|---|---|
| 보드·센서 | ESP32-S3 + PSRAM 탑재 모듈, OV2640/OV5640 등 |
| 빌드 시스템 | ESP-IDF(idf.py) 또는 PlatformIO |
| 해상도/품질 | Pi 추론 입력 크기와 함께 결정(B와 협의) |
| 전송 주기 | 고정 주기 vs 사람 감지 트리거 |
| 시간 | SNTP 서버(Pi를 NTP 서버로 쓸지), 미동기화 시 동작 |
| 설정 저장 | Wi-Fi·토큰·Gateway 주소 저장 방식(NVS 등), 커밋 금지 |

## 측정해서 보고할 것

- 프레임당 JPEG 크기 분포, 촬영→전송 완료 시간, 실제 전송 FPS
- (선택) 사람 감지 지연과 정확도
- 힙/PSRAM 여유량, 장시간 리셋 여부

## 예정 구조 (구현 단계)

구현 시 빌드 시스템 관례에 따라 `main/`, `components/` 등을 추가한다. 빌드 산출물은 `.gitignore`에 등록되어 있다.
