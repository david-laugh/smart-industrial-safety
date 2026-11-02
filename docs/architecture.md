# 아키텍처

> 상태: 초기 설계(v0). 실측 결과에 따라 갱신한다.

## 1. 목표와 범위

- ESP32-S3가 촬영한 JPEG로 사람을 감지하고, Raspberry Pi 5에서 사람별 안전모·안전조끼 착용 상태를 최종 판정한다.
- 미착용(위반) 이벤트만 로컬에 저장하고 Supabase(Database + 비공개 Storage)에 중복 없이 업로드한다.
- 웹 대시보드에서 이벤트와 증거 이미지를 확인한다.

범위 밖(현 단계): 실시간 영상 스트리밍, 얼굴 인식/개인 식별, 출입 통제 연동, 다중 Pi 클러스터.

## 2. 컴포넌트

| 컴포넌트 | 위치 | 책임 | 주요 결정 |
|---|---|---|---|
| Camera Firmware | ESP32-S3 | 촬영, JPEG 인코딩, (선택) Edge Impulse 사람 감지, HTTP multipart 업로드, 백오프 | PSRAM 탑재 모듈 전제. 사람 감지는 **전송 트리거 힌트**이지 최종 판정이 아님 |
| Gateway | Pi / edge-core | HTTP 수신, 인증, 메타데이터·JPEG 검증, `frame_id`·수신 시각 부여, 프레임 선별, 백프레셔 응답 | 프레임은 디스크에 쓰지 않고 메모리 큐(크기·바이트 상한)에 둔다 |
| Frame Selector | Pi / edge-core (gateway 내부) | 장치별 최신 프레임 우선, 오래된 프레임 드롭, 사람 힌트 우선순위 | 큐가 차면 **오래된 것부터 드롭**(latest-wins). 드롭 수를 기록 |
| Inference | Pi / edge-core | JPEG 디코드, 전처리, ONNX Runtime 추론, 사람-PPE 연관, 사람별 상태 산출 | ONNX Runtime CPU EP, ARM64. 세션 1개를 프로세스 수명 동안 재사용 |
| PPE Decision | Pi / edge-core | 다중 프레임 평활화, 위반 판정, 의미적 중복 억제, `event_id` 생성, 증거 이미지·이벤트 JSON 기록 | UNKNOWN은 기본적으로 위반이 아님(정책 파라미터) |
| Event Store | Pi / cloud-sync | spool 수집, SQLite(WAL) outbox, 이미지 보관, 보존 정책 | 단일 쓰기 프로세스(cloud-sync)만 SQLite를 연다 |
| Cloud Sync | Pi / cloud-sync | Storage 업로드 → DB upsert, 지수 백오프 재시도, 멱등성, dead-letter | 이미지 먼저, 행은 나중(대시보드에 이미지 없는 행이 보이지 않게) |
| Supabase | Cloud | `ppe_events` 테이블, 비공개 버킷 `ppe-evidence`, RLS | 대시보드는 Signed URL로만 이미지 접근 |
| Dashboard | Web | 이벤트 목록/필터/상세, 증거 이미지 확인 | 읽기 전용, 인증 사용자만 |

## 3. 데이터 흐름

```
[ESP32] capture ─(1) multipart: metadata JSON + image JPEG─▶ [Gateway]
[Gateway] validate, frame_id, gateway_received_at_utc ─(2) edge-frame─▶ [Frame Selector/Queue] ─▶ [Inference]
[Inference] persons[{bbox, helmet, vest, confidence}] ─(3) inference-result─▶ [PPE Decision]
[PPE Decision] (위반 확정 시) JPEG 파일 + ppe-event JSON ─(4) spool─▶ [Event Store/Cloud Sync]
[Cloud Sync] Storage PUT (event_id 경로) → ppe_events upsert(on_conflict=event_id) ─(5)─▶ [Supabase]
[Dashboard] select ppe_events + createSignedUrl ─▶ 사용자
```

번호는 [protocols.md](protocols.md)의 인터페이스 번호와 같다.

### 3.1 백프레셔와 드롭 정책

- 추론이 입력 FPS를 따라가지 못하면 Gateway 큐가 차고, 오래된 프레임부터 버린다. 지연이 누적되지 않게 하는 것이 정확히 모든 프레임을 처리하는 것보다 우선이다.
- 큐 상한은 **프레임 수와 바이트 수 모두**로 제한한다(`GATEWAY_QUEUE_MAX_FRAMES`, `GATEWAY_QUEUE_MAX_BYTES`).
- 큐가 가득 차거나 장치별 전송률 상한을 넘으면 `429` + `retry_after_ms`로 ESP32에 속도 저하를 요청한다.

### 3.2 Decision → Cloud Sync를 spool로 분리한 이유

- cloud-sync 장애/재시작/네트워크 단절이 추론 경로를 막지 않는다.
- 파일은 `tmp`에 쓰고 `rename`으로 원자적으로 게시한다(JPEG 먼저, JSON 나중). cloud-sync는 JSON이 보일 때만 수집한다.
- 컨테이너 2개가 같은 SQLite를 동시에 쓰는 상황을 피한다.

## 4. 배포 토폴로지

### 4.1 기본: 컨테이너 2개

| 컨테이너 | 포함 모듈 | mem_limit | 네트워크 | 볼륨 |
|---|---|---|---|---|
| `edge-core` | gateway, frame selector, inference, ppe_decision | `1900m` | LAN 포트 노출(Gateway HTTP) | `/data`(spool·images, rw), `/models`(ro) |
| `cloud-sync` | event_store, cloud_sync | `192m` | 외부 HTTPS만 | `/data`(rw) |

장점: 프레임이 프로세스 경계를 넘지 않아 복사·직렬화 비용이 없고, Python 런타임이 한 번만 적재된다.
단점: Gateway와 추론의 장애 격리가 약하다(추론 OOM 시 수신도 중단).

템플릿: [infra/docker/compose.template.yml](../infra/docker/compose.template.yml)

### 4.2 선택: 컨테이너 3개

| 컨테이너 | 포함 모듈 | mem_limit (초기안) |
|---|---|---|
| `gateway` | gateway, frame selector | `256m` |
| `inference` | inference, ppe_decision | `1600m` |
| `cloud-sync` | event_store, cloud_sync | `192m` |

- gateway ↔ inference: 공유 볼륨의 **Unix domain socket**으로 `edge-frame` JSON 헤더 + JPEG 바이트를 길이 접두 프레임으로 전송(Base64 금지). 대안은 `/dev/shm` 공유 메모리.
- 합계 `256m + 1600m = 1856m` 로 기본 배포의 `edge-core 1900m` 이하로 유지해 호스트 예산을 바꾸지 않는다. 대신 런타임 중복 적재로 실제 사용량이 늘 수 있으므로 **반드시 측정**한다.
- 선택 기준: 추론 크래시가 수신을 끊는 문제가 실측에서 확인되거나, 추론 모듈을 독립 교체·재시작할 필요가 있을 때.

템플릿: [infra/docker/compose.split.template.yml](../infra/docker/compose.split.template.yml)

### 4.3 대안: native / systemd

Docker 없이 Python 가상환경 + systemd 서비스 2개(`ppe-edge-core.service`, `ppe-cloud-sync.service`)로 실행한다. 메모리 상한은 cgroup v2 `MemoryMax=`로 동일하게 적용한다. 상세: [infra/systemd/README.md](../infra/systemd/README.md)

| 비교 항목 | Docker | native/systemd |
|---|---|---|
| 메모리 오버헤드 | dockerd + containerd + shim 상주 | 없음(systemd만) |
| 의존성 격리 | 이미지로 고정 | venv + OS 패키지 의존 |
| 배포/롤백 | 이미지 태그 | 디렉터리/패키지 버전 |
| 메모리 상한 | `mem_limit` (cgroup v2) | `MemoryMax=` (cgroup v2) |

어느 쪽을 표준으로 할지는 [benchmark-plan.md](benchmark-plan.md) §8의 비교 측정 후 결정한다.

## 5. 기술 선택(초기안, 변경 가능)

| 영역 | 초기안 | 비고 |
|---|---|---|
| ESP32-S3 | ESP-IDF + esp32-camera, (선택) Edge Impulse SDK | PlatformIO 또는 idf.py는 A가 결정 |
| Edge 언어 | Python 3.11+ (ARM64) | 추론 핫패스는 NumPy 벡터화 |
| HTTP 서버 | 경량 ASGI 서버 1 worker | 워커 수 증가 = 메모리 증가이므로 1부터 측정 |
| 추론 | ONNX Runtime CPU EP | `intra_op_num_threads` 초기값 3(코어 4개 중 1개는 수신/동기화용) — 측정 대상 |
| 모델 | 사람/안전모/조끼를 검출하는 단일 경량 검출기 또는 사람 검출 + PPE 분류 2단계 | B가 정확도·지연·메모리 측정 후 결정. 가중치는 저장소에 넣지 않음 |
| 로컬 저장 | SQLite(WAL) + 파일 시스템 | 가능하면 NVMe/USB SSD, SD카드면 쓰기량 최소화 |
| 클라우드 | Supabase Postgres + Storage(private) | |
| 대시보드 | 정적 SPA + Supabase JS 클라이언트 | C가 결정 |

## 6. 실패 모드와 대응

| 실패 | 영향 | 대응 |
|---|---|---|
| Wi-Fi 끊김 (ESP32↔Pi) | 프레임 유실 | ESP32는 오래된 프레임을 재전송하지 않고 최신 프레임만 보냄. 재연결 백오프 |
| 인터넷 끊김 (Pi↔Supabase) | 업로드 지연 | outbox에 누적, 복구 후 재시도. 디스크 상한 초과 시 보존 정책 적용 |
| Supabase 5xx/429 | 업로드 지연 | 지수 백오프 + 지터, `Retry-After` 존중 |
| 추론 지연 증가(열 스로틀링) | 큐 증가, 드롭 증가 | 드롭/큐 길이 메트릭, 온도·스로틀 상태 기록 |
| edge-core OOM | 수신·추론 중단 | `restart: unless-stopped`, OOM 이벤트 기록. 재발 시 3-컨테이너 분리 검토 |
| Pi 재부팅/전원 차단 | 미전송 이벤트 | spool/SQLite 영속화, `event_id` 불변으로 재업로드 시 중복 없음 |
| Pi 시계 미동기화 | 잘못된 시각 | `gateway_clock_synced=false` 표기, Pi 5 RTC 배터리 사용 권장 |

## 7. 보안

- ESP32 → Gateway: 장치별 Bearer 토큰, 전용 SSID/VLAN 권장. TLS는 ESP32 메모리 비용 측정 후 결정.
- Gateway 포트는 현장 LAN에만 바인딩한다(`GATEWAY_BIND_ADDR`).
- Supabase 키: 대시보드는 anon key + RLS만 사용. 업로드용 고권한 키는 cloud-sync에만, 파일 권한 0600 또는 Docker secret. 장기적으로 장치별 제한 자격증명/Edge Function 경유를 검토([cloud/README.md](../cloud/README.md)).
- 증거 이미지는 개인정보가 될 수 있으므로 비공개 버킷 + 짧은 Signed URL + 보존 기간을 둔다.
