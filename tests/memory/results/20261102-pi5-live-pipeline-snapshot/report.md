# 측정 보고서: Pi 5 실동작 파이프라인 메모리 스냅샷 (2026-11-02)

- 담당: D · 방식: `/proc` 읽기 전용(프로세스 중지·재시작·설정 변경 없음)
- 기간: 2026-11-02T07:12:49Z ~ 07:13:49Z (16:12 ~ 16:13 KST), 1초 간격 60샘플 + 시작 시점 스냅샷
- 상태: XIAO → Gateway 프레임 수신 중(측정 종료 시 accepted 5,080, events 29), cloud-sync 상주 업로드 중
- **판정용 측정 아님**: native, Docker 없음, 대시보드는 PC 에서 실행(미포함), 60초 단기

## 환경

| 항목 | 값 |
|---|---|
| 보드 / OS | Raspberry Pi 5 4GB, Debian 13 trixie, 커널 6.18.50, Python 3.13.5, 부팅 후 4시간 36분 |
| 실행 | native venv `~/ppe-gw`, nohup 수동 실행 |
| `edge.gateway` | 수신 + 추론(`ORT_INTRA_OP_THREADS=2`) + 판정, 실행 4,808 s |
| `edge.cloud_sync` | event_store 수집 + Supabase 업로드, 실행 1,513 s |
| cgroup | 메모리 컨트롤러 비활성(`cgroup_disable=memory`) → 프로세스 상한 미적용, `/proc/pressure/memory` 없음 |

## 호스트

| 지표 | 바이트 | 비고 |
|---|---|---|
| 사용량 `MemTotal − MemAvailable` min / mean / max | 407,650,304 / 420,359,918 / **436,371,456** | max ≈ 416.2 MiB |
| 3,500,000,000 B 까지 여유 (max 기준) | 3,063,628,544 | |
| MemTotal | 4,245,766,144 | |
| MemAvailable (스냅샷) | 3,826,810,880 | |
| MemFree (스냅샷) | 2,491,056,128 | |
| Cached (스냅샷) | 1,288,519,680 | 회수 가능 페이지 캐시 |
| Shmem | 18,661,376 | |
| Swap 사용 | 0 | zram 2 GiB |
| Idle 기준선 대비 (평균) | +169,134,762 | Idle 평균 251,225,156 B ([idle baseline](../20261102-pi5-idle-baseline/report.md)) |

온도 62.0 °C, `get_throttled=0x0e0000`(과거 발생 이력 비트, 현재 스로틀 없음), load average 0.46.

## 프로세스

| 프로세스 | RSS | PSS (anon / file) | RSS max (60 s) | VmHWM | 스레드 | 평균 CPU (1코어 기준) |
|---|---|---|---|---|---|---|
| `edge.gateway` | 178,094,080 | 169,266,176 (120,143,872 / 49,122,304) | **190,283,776** | 189,022,208 | 13 | 44.4 % |
| `edge.cloud_sync` | 34,816,000 | 25,424,896 (19,136,512 / 6,288,384) | **34,816,000** | 34,816,000 | 1 | 0.0 % |

Swap 0. 그 밖의 상위 RSS: NetworkManager 22,462,464, systemd 16,089,088, udisksd 15,548,416, systemd-journal 13,647,872 B.

## 예산 대비 (docs/memory-budget.md, 초기 가정)

| 항목 | 예산 | 실측 | 비율 |
|---|---|---|---|
| edge-core (gateway + 추론 + 판정) | 1900 MiB = 1,992,294,400 B | 190,283,776 B | 9.6 % |
| cloud-sync | 192 MiB = 201,326,592 B | 34,816,000 B | 17.3 % |
| 호스트 전체 | < 3,500,000,000 B | 436,371,456 B | 12.5 % |

## 판정

- 실동작(XIAO 수신 + 추론 + Supabase 업로드) 단기 구간에서 호스트 최대 436,371,456 B, swap 0 → 3.5 GB 목표 대비 여유 큼
- **SYS-MEM-01 최종 판정은 보류**: Docker 데몬, 대시보드(Pi 배치 시), 60분 이상 장시간 추이가 빠짐
- SYS-MEM-03/04(컨테이너 상한)는 메모리 cgroup 비활성으로 측정 불가

## 후속

1. 같은 방식으로 60분 이상 샘플링 → 웜업 후 RSS·호스트 사용량 증가 기울기(SYS-MEM-05)
2. 메모리 cgroup 활성화(재부팅 필요, 승인 대기) → systemd `MemoryMax` 또는 Docker `mem_limit` 적용 후 재측정
3. Docker Lite 배포에서 같은 부하로 비교
