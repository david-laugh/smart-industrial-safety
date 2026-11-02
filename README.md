# 스마트 산업 안전 시스템 (PPE Safety Edge)

ESP32-S3 카메라가 촬영한 이미지로 작업자를 감지하고, Raspberry Pi 5(4GB)에서 **안전모·안전조끼 착용 상태**를 최종 판정하여 미착용 이벤트를 Supabase에 자동 기록하는 엔드투엔드 IoT 시스템.

> **현재 상태: Repository Skeleton (2026-11-02)**
> 이 저장소에는 디렉터리 구조, 문서, JSON Schema 계약, 비실행 Docker Compose 템플릿, 환경변수 템플릿만 있다.
> 실행 코드, 펌웨어, 추론 코드, 테스트 코드, 스크립트, 모델 가중치, 성능 측정 결과는 **아직 없다.**
> 문서에 나오는 메모리·성능 수치는 모두 **검증 전 초기 예산/목표**이며 측정값이 아니다.

## 시스템 개요

```
┌──────────────┐  HTTP multipart   ┌──────────────────── Raspberry Pi 5 (4GB, ARM64) ─────────────────────┐
│  ESP32-S3    │  metadata(JSON)   │ ┌──────────── edge-core (mem_limit 1900m) ────────────┐               │
│  카메라 촬영   │  + image(JPEG)    │ │ gateway ─▶ frame selector ─▶ inference ─▶ ppe_decision│               │
│  JPEG 생성    ├──────────────────▶│ │ (수신/검증)    (큐/드롭)     (ONNX Runtime)  (평활화/판정)│               │
│  (선택) 사람감지│◀── 202/429/4xx ───┤ └──────────────────────────────────────────┬─────────┘               │
└──────────────┘                   │                          event JSON + JPEG  │ (공유 볼륨 spool)        │
                                   │ ┌──────────── cloud-sync (mem_limit 192m) ──▼──────────┐               │
                                   │ │ event_store(SQLite outbox) ─▶ cloud_sync(재시도/멱등)   │               │
                                   │ └───────────────────────────────────────┬──────────────┘               │
                                   └─────────────────────────────────────────┼──────────────────────────────┘
                                                                             │ HTTPS (event_id 멱등 upsert)
                                                       ┌─────────────────────▼──────────────────────┐
                                                       │ Supabase: Postgres(ppe_events) + 비공개 Storage│
                                                       └─────────────────────┬──────────────────────┘
                                                                             │ 인증 사용자 + Signed URL
                                                                     ┌───────▼───────┐
                                                                     │ Web Dashboard │
                                                                     └───────────────┘
```

## 핵심 제약

| 항목 | 값 |
|---|---|
| 대상 하드웨어 | Raspberry Pi 5 **4GB**, ARM64 Linux (Raspberry Pi OS 64-bit 기준) |
| 호스트 전체 메모리 목표 | **3,500,000,000 바이트 미만** (MemTotal − MemAvailable, 테스트 중 최대값) |
| 기본 배포 | 컨테이너 2개: `edge-core`(1900m) + `cloud-sync`(192m) |
| 대안 배포 | 컨테이너 3개(gateway / inference / cloud-sync), native/systemd |
| PPE 상태 값 | `WORN`, `MISSING`, `UNKNOWN` |
| 시간 | UTC(RFC 3339, `Z`), MCU uptime과 Gateway 수신 시각 분리 |
| 이미지 전송 | multipart 바이너리. **JSON Base64 금지** |
| 이벤트 ID | Decision 단계에서 1회 생성, 재전송 시에도 불변 |

## 디렉터리 구조

| 경로 | 내용 | 담당 |
|---|---|---|
| `firmware/esp32-s3/` | 카메라 촬영, JPEG, (선택) Edge Impulse 사람 감지, HTTP 업로드 | A |
| `edge/gateway/` | 프레임 수신·검증, 프레임 선별, 백프레셔 | C |
| `edge/inference/` | ONNX Runtime 추론, 사람-PPE 연관 | B |
| `edge/ppe_decision/` | 시간적 평활화, 위반 판정, 이벤트 생성 | B |
| `edge/event_store/` | 로컬 이벤트/이미지 저장, outbox | C |
| `edge/cloud_sync/` | Supabase 업로드, 재시도, 멱등성 | C |
| `cloud/` | Supabase 테이블·Storage·RLS 설계 | C |
| `dashboard/` | 이벤트 확인용 웹 대시보드 | C |
| `infra/docker/` | Compose 템플릿(2/3 컨테이너), `.env.example` | D |
| `infra/systemd/` | native/systemd 배포 설계 | D |
| `contracts/` | 인터페이스 JSON Schema + 예시 | D(관리), 각 생산자(내용) |
| `tests/contract/` | 계약 테스트 계획 | D |
| `tests/integration/` | 통합 테스트 계획 | D |
| `tests/performance/` | 성능 측정 계획/결과 보관 위치 | D |
| `tests/memory/` | 메모리 안정성 테스트 계획 | D |
| `docs/` | 설계 문서 | 전원 |

## 문서

| 문서 | 내용 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 컴포넌트, 데이터 흐름, 배포 토폴로지(2/3 컨테이너, native) |
| [docs/roles.md](docs/roles.md) | 역할 A~D 책임, 디렉터리, 입출력, 통합 기준 |
| [docs/protocols.md](docs/protocols.md) | 인터페이스 1~5 상세, 시간 규칙, 중복 방지, 재시도 |
| [docs/memory-budget.md](docs/memory-budget.md) | 메모리 예산(바이트 단위 계산), 측정 정의 |
| [docs/benchmark-plan.md](docs/benchmark-plan.md) | Raspberry Pi 5 실기 성능 측정 계획 |
| [docs/acceptance-criteria.md](docs/acceptance-criteria.md) | 컴포넌트/시스템 수용 기준 |
| [docs/integration-plan.md](docs/integration-plan.md) | 마일스톤, 통합 순서, 계약 변경 절차 |
| [contracts/README.md](contracts/README.md) | 계약 스키마 목록과 버전 규칙 |

## 작업 원칙

1. **계약 우선**: 컴포넌트 간 데이터는 `contracts/`의 JSON Schema가 단일 기준이다. 스키마 변경은 [docs/integration-plan.md](docs/integration-plan.md)의 절차를 따른다.
2. **측정 전에는 주장하지 않는다**: 성능·메모리 수치는 Raspberry Pi 5 실기 ARM64 측정 결과로만 보고한다. 개발 PC(x86) 결과는 참고용이다.
3. **비밀 정보 커밋 금지**: `.env`, Supabase 키, 장치 토큰, 모델 가중치는 커밋하지 않는다(`.gitignore` 참고).
4. **호스트 전체를 본다**: 컨테이너 `mem_limit` 준수만으로는 부족하다. 호스트 전체 메모리를 함께 측정한다.
