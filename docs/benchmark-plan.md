# 성능 측정 계획

> 이 문서는 **계획**이다. 측정 결과는 아직 없으며, 수치를 추정하거나 다른 장치 결과로 대체하지 않는다.
> 모든 판정용 측정은 **Raspberry Pi 5 (4GB) 실기, ARM64**에서 수행한다. 개발 PC(x86)·에뮬레이션(QEMU) 결과는 판정에 쓰지 않는다.

## 1. 측정 목적

1. 호스트 전체 메모리 < 3,500,000,000 바이트를 장시간 유지하는지 확인
2. 목표 입력 FPS에서 지연·드롭이 허용 범위인지 확인
3. 네트워크 장애 시 이벤트 유실/중복이 없는지 확인
4. Docker와 native/systemd 배포 중 표준을 결정

## 2. 테스트 환경 기록 (모든 보고서 필수)

| 항목 | 기록 예 |
|---|---|
| 보드 | Raspberry Pi 5, RAM 4GB, 보드 리비전 |
| OS / 커널 | Raspberry Pi OS 64-bit 버전, `uname -a` |
| 저장장치 | microSD / NVMe / USB SSD, 모델 |
| 냉각 | 공식 Active Cooler 여부, 케이스 |
| 전원 | 공식 27W PSU 여부, 저전압 경고 유무 |
| 주변 온도 | ℃ |
| swap | 종류·크기 |
| 배포 방식 | Docker 2-컨테이너 / 3-컨테이너 / native |
| 소프트웨어 버전 | git commit, 이미지 태그, ONNX Runtime 버전, 모델 이름·버전·sha256 |
| 입력 | ESP32 실기 수, 해상도, JPEG 품질, 목표 FPS / 또는 재생 입력 |
| 네트워크 | Wi-Fi 대역, RSSI, AP |

## 3. 측정 지표

| 지표 | 정의 | 수집 방법(구현 단계에서 도구화) |
|---|---|---|
| 입력 FPS | Gateway가 202로 수락한 프레임 수 / 초, 장치별 | Gateway 카운터 |
| 처리 FPS | 추론 완료 프레임 수 / 초 | Inference 카운터 |
| 디코드·전처리 지연 | `timing.decode_ms + preprocess_ms` | `inference-result.timing` |
| 추론 지연 | `timing.inference_ms` | 동일 |
| 후처리 지연 | `timing.postprocess_ms` | 동일 |
| Pi 내부 지연 | `decided_at_utc − gateway_received_at_utc` (Pi 단일 시계) | 이벤트/로그 |
| End-to-End 지연 | 아래 §4 타임라인의 T0→T5 | 로그 결합 |
| 업로드 지연 | 이벤트 생성 → `SYNCED` | outbox 타임스탬프 |
| 컨테이너 CPU | cgroup `cpu.stat`의 `usage_usec` 증가율 (코어 4개 = 400%) | cgroup 1초 샘플 |
| 컨테이너 메모리 피크 | `memory.peak` 또는 `memory.current` 최대 | cgroup |
| 호스트 메모리 | `MemTotal − MemAvailable` 시계열·최대 | `/proc/meminfo` 1초 샘플 |
| 프레임 드롭 | 선별기 드롭 수, 429 응답 수, 드롭률 | Gateway 카운터 |
| 큐 길이 | 큐 프레임 수/바이트 시계열, 최대 | Gateway 게이지 |
| OOM | 커널 OOM 로그, cgroup `memory.events` `oom_kill` | journal/cgroup |
| 열/스로틀 | SoC 온도, 스로틀 플래그 | `vcgencmd measure_temp`, `vcgencmd get_throttled` |
| 메모리 압박 | PSI `some/full avg10` | `/proc/pressure/memory` |

지연은 평균이 아니라 **p50 / p95 / p99 / max**와 샘플 수로 보고한다. 처음 60초(웜업)는 별도 표기하고 통계에서 제외한다.

## 4. End-to-End 타임라인

| 지점 | 시각 | 시계 |
|---|---|---|
| T0 | ESP32 촬영 (`capture_ts_utc`) | ESP32 SNTP |
| T1 | Gateway 수신 완료 (`gateway_received_at_utc`) | Pi |
| T2 | 추론 시작(큐 대기 종료) | Pi |
| T3 | 추론 완료 | Pi |
| T4 | 판정 완료 (`decided_at_utc`) | Pi |
| T5 | spool 게시 완료 | Pi |
| T6 | Supabase `SYNCED` | Pi |

- **T1→T5**는 Pi 시계만 쓰므로 신뢰도가 높다 → 주 지표.
- **T0→T5**는 ESP32·Pi 시계 오차가 포함된다. 측정 전 두 장치를 같은 NTP 서버에 동기화하고, 오차 추정치(예: 테스트 전후 ESP32가 보고한 시각과 Pi 수신 시각 차이의 분포)를 함께 기록한다.
- T6는 네트워크 의존 지표로 분리 보고한다.

## 5. 입력 조건

| 조건 | 내용 |
|---|---|
| 실기 입력 | ESP32-S3 실제 장치 1대, 이후 목표 대수 |
| 재생 입력 | 고정 JPEG 시퀀스를 같은 multipart 형식으로 재생(반복성 확보용, 재생 도구는 구현 단계에서 D가 작성) |
| FPS 단계 | 목표 FPS의 50% / 100% / 150%(과부하) |
| 장면 | 사람 0명, 1명, 다수, 미착용 포함 — 장면 구성 문서화 |

## 6. 테스트 시나리오

### 6.1 단기 테스트 (10~15분)

- 목적: 설정 조합 비교, 회귀 확인
- 조건별로 각 1회 이상: FPS 단계 × 배포 방식 × ONNX 스레드 수(예: 2, 3, 4)
- 산출: 지표 표 전체, 호스트/컨테이너 메모리 그래프

### 6.2 장시간 메모리 안정성 (60분 이상)

- 목적: 누수·단편화·캐시 증가 확인
- 조건: 목표 FPS 100%, 미착용 이벤트가 주기적으로 발생하는 장면
- 판정 데이터:
  - 호스트 메모리 최대값 (< 3,500,000,000 B)
  - 웜업(첫 10분) 이후 `memory.current`/RSS 선형 회귀 기울기(바이트/시간)
  - OOM, 재시작 횟수
- 가능하면 야간 장시간(수 시간)도 1회 수행

### 6.3 네트워크 장애 및 복구

| 시나리오 | 방법 | 확인 |
|---|---|---|
| WAN 단절 10분 | Pi 상위 라우터 업링크 차단 또는 Supabase 도메인 차단 | outbox 누적, 메모리/디스크 증가량, 복구 후 업로드 시간, 유실 0·중복 0 |
| WAN 단절 60분 | 동일 | 대량 백로그 처리 시 cloud-sync 메모리 피크 |
| Supabase 오류 응답 | 프록시로 429/503 주입 | 백오프 간격, `Retry-After` 준수 |
| ESP32 Wi-Fi 단절 | AP 재시작 | ESP32 재연결 시간, Gateway 정상 유지 |
| cloud-sync 강제 종료 | 업로드 도중 컨테이너/서비스 kill | 재시작 후 중복 없음, 상태 일관성 |
| Pi 전원 차단 | 이벤트 누적 상태에서 전원 차단 후 재부팅 | spool/SQLite 손상 여부, 재업로드 |

### 6.4 과부하·OOM 경계

- 150% FPS, 다수 인원 장면으로 큐/드롭 동작 확인
- (선택) 상한을 의도적으로 낮춰 OOM 시 재시작·복구 동작 확인 — 판정 측정과 분리

## 7. 샘플링 원칙

- 메모리/CPU 샘플 간격 1초, 측정 도구 자체의 CPU·메모리 영향을 기록
- 측정 도구는 컨테이너 밖(호스트)에서 실행하고 해당 사용량은 호스트 사용량에 포함됨을 명시
- 원시 데이터(CSV/JSONL)는 `tests/performance/results/<YYYYMMDD>-<시나리오>/raw/`에 두고 커밋하지 않으며, 요약 보고서만 커밋

## 8. Docker vs native 비교

같은 commit, 같은 모델, 같은 입력, 같은 냉각 조건에서 비교한다.

| 항목 | Docker 2-컨테이너 | Docker 3-컨테이너 | native/systemd |
|---|---|---|---|
| 유휴 시 호스트 메모리 | | | |
| 부하 시 호스트 메모리 최대 | | | |
| edge 컴포넌트 메모리 피크 | | | |
| cloud-sync 메모리 피크 | | | |
| 처리 FPS | | | |
| T1→T5 p50/p95/p99 | | | |
| CPU 사용률 | | | |
| 재시작/복구 시간 | | | |
| 배포·운영 난이도(정성) | | | |

빈 칸은 측정 후 채운다.

## 9. 보고서 템플릿

```
# 측정 보고서: <시나리오> (<YYYY-MM-DD>)
- 환경: §2 표 전체
- 입력: ...
- 기간: 시작/종료 UTC, 웜업 제외 구간
- 결과 표: §3 지표 (p50/p95/p99/max, 샘플 수)
- 메모리: 호스트 최대 <바이트>, 컨테이너별 피크 <바이트>, OOM <건수>
- 그래프: 호스트 메모리, 컨테이너 메모리, 큐 길이, 온도
- 수용 기준 판정: acceptance-criteria.md 항목별 PASS/FAIL
- 이상 사항 / 후속 조치
- 원시 데이터 위치
```
