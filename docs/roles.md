# 역할과 책임

각 역할은 **계약(contracts/)만 보고 독립적으로 개발**하고, 통합 기준을 통과하면 다음 단계로 넘어간다.

## 요약

| 역할 | 이름 | 담당 디렉터리 | 생산하는 계약 | 소비하는 계약 |
|---|---|---|---|---|
| A | MCU / Camera | `firmware/esp32-s3/` | `esp32-frame-metadata` (+ JPEG) | `gateway-response` |
| B | AI / Vision | `edge/inference/`, `edge/ppe_decision/` | `inference-result`, `ppe-event` (+ 증거 JPEG) | `edge-frame` |
| C | Edge / Cloud | `edge/gateway/`, `edge/event_store/`, `edge/cloud_sync/`, `cloud/`, `dashboard/` | `gateway-response`, `edge-frame`, `supabase-event-record` | `esp32-frame-metadata`, `ppe-event` |
| D | Infrastructure / QA / Integration | `infra/`, `contracts/`(관리), `tests/`, `docs/`(관리) | Compose/systemd 설계, 측정 보고서 | 전체 |

---

## A: MCU / Camera

**책임**
- ESP32-S3 카메라 초기화(PSRAM 프레임 버퍼), JPEG 촬영, 해상도·품질 설정
- (선택) Edge Impulse 사람 감지 → `trigger=EDGE_PERSON_DETECTED`로 전송 빈도 조절
- `boot_id` 생성, `frame_seq` 증가, `mcu_uptime_ms` 기록, SNTP 동기화 상태 보고
- HTTP multipart 업로드, 응답 코드별 백오프(`429/503`의 `retry_after_ms` 준수)
- Wi-Fi 재연결, 워치독, 장치 토큰 보관

**담당 디렉터리**: `firmware/esp32-s3/`

**입력**: 카메라 센서, Wi-Fi/SNTP, Gateway 응답(`gateway-response.schema.json`)
**출력**: `POST /api/v1/frames` multipart — `metadata`(`esp32-frame-metadata.schema.json`) + `image`(image/jpeg)

**통합 테스트 기준**
- [ ] 생성한 메타데이터가 계약 스키마를 통과한다(시리얼 로그로 덤프한 샘플 10건 이상).
- [ ] `image.byte_length`, `width`, `height`가 실제 JPEG와 일치한다.
- [ ] 재부팅 후 `boot_id`가 바뀌고 `frame_seq`가 0부터 다시 시작한다.
- [ ] SNTP 미동기화 시 `synced=false`, `capture_ts_utc=null`을 보낸다.
- [ ] `429` 응답 시 `retry_after_ms` 이상 대기 후 재전송한다.
- [ ] Wi-Fi AP 재시작 후 사람 개입 없이 전송이 재개된다.
- [ ] 목표 FPS/해상도에서 30분 연속 동작 중 리셋이 없다(리셋 원인 로그 확인).

---

## B: AI / Vision

**책임**
- PPE 모델 선정·학습·ONNX 변환·양자화 검토(모델 카드에 데이터셋, 클래스, 입력 크기, 라이선스 기록)
- 추론 파이프라인: JPEG 디코드 → 리사이즈/정규화 → ONNX Runtime → NMS → 사람-PPE 연관
- 사람별 `helmet`/`vest` 상태(WORN/MISSING/UNKNOWN)와 신뢰도 산출
- PPE Decision: 다중 프레임 평활화, 임계값, UNKNOWN 정책, 의미적 중복 억제(`dedup_key`), `event_id` 생성
- 증거 JPEG와 `ppe-event` JSON을 spool에 원자적으로 기록
- 단계별 지연(`timing`) 기록

**담당 디렉터리**: `edge/inference/`, `edge/ppe_decision/`

**입력**: `edge-frame.schema.json` (+ JPEG 바이트)
**출력**: `inference-result.schema.json`(내부), `ppe-event.schema.json` + `images/…/{event_id}.jpg`(spool)

**통합 테스트 기준**
- [ ] 고정된 평가 이미지 세트에서 클래스별 정밀도/재현율을 보고한다(세트 구성과 라벨 기준 문서화).
- [ ] Raspberry Pi 5 ARM64에서 측정한 전처리/추론/후처리 지연 분포(p50/p95/p99)를 보고한다.
- [ ] 추론 프로세스 단독 RSS/메모리 피크를 보고한다.
- [ ] 출력이 `inference-result`, `ppe-event` 스키마를 100% 통과한다.
- [ ] UNKNOWN만 있는 사람은 기본 정책에서 이벤트를 만들지 않는다.
- [ ] 같은 사람이 `dedup_window_s` 동안 계속 미착용이어도 이벤트는 1건이다.
- [ ] 같은 입력 시퀀스를 다시 넣으면 판정 결과가 같다(결정적 동작, `event_id` 제외).

---

## C: Edge / Cloud

**책임**
- Gateway: HTTP 엔드포인트, 장치 인증, 메타데이터/JPEG 검증, `frame_id`·`gateway_received_at_utc` 부여, 프레임 선별 큐, 백프레셔
- Event Store: spool 수집, SQLite outbox(상태 머신), 이미지 보존/정리 정책, 디스크 상한
- Cloud Sync: Storage 업로드 → DB upsert, 재시도/백오프, 멱등성, dead-letter, 복구
- Supabase: 테이블/인덱스/RLS/버킷 정책 설계와 적용(마이그레이션은 구현 단계에서 `cloud/`에 추가)
- Dashboard: 이벤트 목록·필터·상세, Signed URL 이미지 표시

**담당 디렉터리**: `edge/gateway/`, `edge/event_store/`, `edge/cloud_sync/`, `cloud/`, `dashboard/`

**입력**: `esp32-frame-metadata` + JPEG, `ppe-event` + JPEG 파일
**출력**: `gateway-response`, `edge-frame`, `supabase-event-record`, Storage 객체

**통합 테스트 기준**
- [ ] 계약 위반 메타데이터, 크기 불일치, 비 JPEG, 상한 초과에 정의된 오류 코드로 응답한다.
- [ ] 큐 상한 도달 시 오래된 프레임을 드롭하고 드롭 수를 기록하며 메모리가 상한 내에 머문다.
- [ ] 네트워크 단절 10분 후 복구 시 누적 이벤트가 **유실 0건, 중복 0건**으로 업로드된다.
- [ ] 같은 이벤트를 강제로 2회 업로드해도 Supabase 행 1개, 객체 1개다.
- [ ] cloud-sync를 업로드 도중 강제 종료 후 재시작해도 상태가 일관된다.
- [ ] 대시보드는 인증 없이 데이터/이미지에 접근할 수 없다.
- [ ] cloud-sync 메모리 피크가 `192m` 상한 대비 여유를 가진다(측정값 보고).

---

## D: Infrastructure / QA / Integration

**책임**
- Raspberry Pi 5 기준 이미지/OS 설정(cgroup v2 메모리 컨트롤러, swap 정책, NTP, 쿨러)
- Docker Compose 템플릿(2/3 컨테이너), native/systemd 설계, 환경변수 관리
- 계약 저장소 관리: 스키마 리뷰, 버전 규칙, 계약 테스트 구현
- 통합/성능/메모리/장애 테스트 실행과 보고서 작성
- 수용 기준 판정, 통합 일정 관리

**담당 디렉터리**: `infra/docker/`, `infra/systemd/`, `contracts/`(관리), `tests/contract/`, `tests/integration/`, `tests/performance/`, `tests/memory/`, `docs/`(관리)

**입력**: 모든 컴포넌트 산출물, 계약 스키마
**출력**: 배포 템플릿, 테스트 계획/결과 보고서, 수용 판정

**통합 테스트 기준**
- [ ] 모든 계약 예시와 각 역할의 실제 출력 샘플이 계약 테스트를 통과한다.
- [ ] Raspberry Pi 5 실기에서 [benchmark-plan.md](benchmark-plan.md)의 측정을 수행하고 원시 데이터와 요약을 남긴다.
- [ ] 60분 이상 장시간 테스트에서 호스트 메모리 < 3,500,000,000 바이트, OOM 0건을 확인한다.
- [ ] Docker와 native 배포를 같은 조건에서 비교한 보고서를 작성한다.
- [ ] [acceptance-criteria.md](acceptance-criteria.md)의 시스템 수준 기준 판정표를 작성한다.

---

## 역할 간 인터페이스 소유권

| 인터페이스 | 스키마 | 생산자 | 소비자 | 변경 승인 |
|---|---|---|---|---|
| 1 | `esp32-frame-metadata` | A | C | A + C + D |
| 1 응답 | `gateway-response` | C | A | A + C + D |
| 2 | `edge-frame` | C | B | B + C + D |
| 3 | `inference-result` | B | B | B + D |
| 4 | `ppe-event` | B | C | B + C + D |
| 5 | `supabase-event-record` | C | C(대시보드), D | C + D |

## 소통 규칙

- 계약 변경은 PR 단위로 제안하고 위 표의 승인자가 모두 리뷰한다.
- 측정 결과는 장치 정보(OS, 커널, 쿨러, 전원, 저장장치)와 함께 보고한다.
- 블로커는 다른 역할의 구현을 기다리지 말고 `contracts/examples/`로 대체 입력을 만들어 진행한다.
