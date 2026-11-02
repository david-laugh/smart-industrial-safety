# 메모리 예산

> **중요: 이 문서의 모든 수치는 측정값이 아니라 검증해야 할 초기 예산이다.**
> 실측 결과는 [benchmark-plan.md](benchmark-plan.md)의 절차로 수집하고, 이 문서를 갱신할 때 측정 보고서를 링크한다.

## 1. 목표

**호스트 전체 메모리 사용량 < 3,500,000,000 바이트** (Raspberry Pi 5 4GB, 테스트 전체 구간의 최대값 기준)

컨테이너 `mem_limit`를 지키는 것만으로는 목표 달성을 보장하지 않는다. 커널, 페이지 캐시 일부, dockerd/containerd, systemd, 로그, tmpfs 등은 컨테이너 상한 밖에 있다. 따라서 **컨테이너별 피크와 호스트 전체 사용량을 모두** 측정한다.

## 2. 단위 주의

| 표기 | 의미 | 바이트 |
|---|---|---|
| Docker `1900m` | 1900 **MiB** (Docker는 `m`을 2^20으로 해석) | 1,992,294,400 |
| Docker `192m` | 192 MiB | 201,326,592 |
| systemd `MemoryMax=1900M` | 1900 MiB (systemd도 1024 기준) | 1,992,294,400 |
| 목표 3,500,000,000 B | 10진 바이트 | ≈ 3,337.86 MiB |
| 물리 RAM "4GB" | 4 GiB | 4,294,967,296 (단, OS가 보는 `MemTotal`은 펌웨어/커널 예약으로 더 작음 → 실측 기록) |

## 3. 초기 예산 (기본 2-컨테이너)

| 항목 | 상한/예산 | 바이트 | 성격 |
|---|---|---|---|
| `edge-core` | `mem_limit: 1900m` | 1,992,294,400 | cgroup 하드 상한 |
| `cloud-sync` | `mem_limit: 192m` | 201,326,592 | cgroup 하드 상한 |
| OS + Docker 데몬 + 시스템 서비스 | 목표 800 MB (보수적으로 800 MiB로 계산) | 838,860,800 | 목표치(강제 아님) |
| **소계** | | **3,032,481,792** | |
| 목표 대비 여유 (3,500,000,000 − 소계) | | **467,518,208** (≈ 445.9 MiB) | 버스트·운영 여유 |
| 목표 초과분 ~ 물리 RAM 사이 | | `MemTotal` − 3,500,000,000 | 최후 안전 여유(사용 계획 없음) |

해석:
- 두 컨테이너가 동시에 상한까지 차고 OS가 예산을 지켜도 목표 대비 약 446 MiB가 남는다.
- OS+Docker 800 MB는 **목표치**다. 실측이 이를 넘으면 여유가 줄어들므로, 초과 시 컨테이너 상한을 낮추거나 native 배포를 검토한다.
- `mem_limit`는 상한이지 예약이 아니다. 실제 사용량이 상한보다 훨씬 낮으면 여유는 더 커진다.

## 4. 3-컨테이너 분리 시 (선택)

| 항목 | 상한 | 바이트 |
|---|---|---|
| `gateway` | `256m` | 268,435,456 |
| `inference` | `1600m` | 1,677,721,600 |
| `cloud-sync` | `192m` | 201,326,592 |
| OS + Docker | 800 MiB | 838,860,800 |
| **소계** | | **2,986,344,448** |
| 목표 대비 여유 | | 513,655,552 |

- 상한 합계는 기본안보다 작지만 Python 런타임/라이브러리 중복 적재, 프로세스 간 전송 버퍼로 **실사용량은 늘 수 있다.** `inference 1600m`이 추론 피크를 수용하는지 반드시 측정한다.

## 5. native/systemd 시

- Docker 데몬 계열 상주 메모리가 사라진다(크기는 측정 대상).
- 같은 상한을 `MemoryMax=1900M`, `MemoryMax=192M`로 적용하고 `MemorySwapMax=0`으로 swap을 막는다.

## 6. 측정 정의 (모든 보고서에서 동일하게 사용)

| 지표 | 정의 | 출처 |
|---|---|---|
| **호스트 사용량** | `MemTotal − MemAvailable` (kB × 1024 → 바이트) | `/proc/meminfo` |
| 호스트 피크 | 1초 간격 샘플의 최대값 | 위 값의 시계열 |
| 컨테이너 현재 사용량 | cgroup v2 `memory.current` | `/sys/fs/cgroup/.../memory.current` |
| 컨테이너 피크 | cgroup v2 `memory.peak` (지원 커널) — 미지원 시 `memory.current` 1초 샘플 최대값 | cgroup |
| 컨테이너 구성 | `memory.stat`의 `anon`, `file`, `kernel`, `shmem` | cgroup |
| 프로세스 RSS/PSS | `/proc/<pid>/smaps_rollup` | procfs |
| swap | `SwapTotal − SwapFree`, `pswpin/pswpout` 증가량 | `/proc/meminfo`, `/proc/vmstat` |
| OOM | 커널 로그의 OOM 메시지, cgroup `memory.events`의 `oom`, `oom_kill` | `dmesg`/journal, cgroup |
| 메모리 압박 | `/proc/pressure/memory` (PSI) | procfs |

참고: `docker stats`의 MEM USAGE는 버전에 따라 페이지 캐시 제외 방식이 다르므로 **보조 지표로만** 쓰고, 판정은 cgroup 원본 값과 `/proc/meminfo`로 한다.

## 7. 사전 점검 항목 (측정 전)

- [ ] `docker info`에 메모리 제한 미지원 경고가 없는지 확인. 경고가 있으면 커널 cmdline에 cgroup 메모리 컨트롤러 활성화 필요 여부 검토.
- [ ] cgroup v2 사용 여부(`/sys/fs/cgroup/cgroup.controllers`에 `memory` 포함).
- [ ] swap 구성(dphys-swapfile/zram 여부와 크기) 기록. 컨테이너는 `memswap_limit = mem_limit`으로 swap 사용 금지.
- [ ] `tmpfs` 마운트는 메모리를 쓰므로 크기 상한을 명시(컨테이너 tmpfs는 해당 cgroup에 과금).
- [ ] 로깅 드라이버 크기 제한(`max-size`, `max-file`) 설정.
- [ ] GUI(데스크톱) 비활성화 여부 기록 — Lite/headless 권장.

## 8. 메모리 절감 레버 (초과 시 검토 순서)

1. 추론 입력 해상도 축소, 모델 경량화/양자화(정확도 재측정 필요)
2. ONNX Runtime 세션 옵션: 메모리 패턴/arena 설정, 스레드 수 조정
3. Gateway 큐 바이트 상한 축소, 장치당 대기 프레임 1장
4. HTTP 워커 1개 유지, 불필요한 라이브러리 import 제거
5. native/systemd 배포로 Docker 데몬 오버헤드 제거
6. 데스크톱/불필요 서비스 비활성화

## 9. 갱신 기록

| 날짜 | 변경 | 근거 |
|---|---|---|
| 2026-11-02 | 초기 예산 작성 | 설계 가정(측정 전) |
