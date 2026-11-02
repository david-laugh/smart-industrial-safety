# 측정 보고서: Raspberry Pi 5 Idle 기준선 + 환경 분석 (2026-11-02)

- 담당: D · 실행 방식: SSH 읽기 전용 명령 (시스템 변경 없음)
- 기간: 2026-11-02T03:06:04Z ~ 03:09:03Z (부팅 후 약 29분), 1초 간격 180샘플
- 원시 데이터: `raw/idle_meminfo.csv` (커밋하지 않음)

## 환경 (benchmark-plan.md §2)

| 항목 | 값 |
|---|---|
| 보드 | Raspberry Pi 5 Model B Rev 1.1 (Revision c04171), RAM 4GB |
| OS | Raspberry Pi OS (Debian 13 trixie), 이미지 2026-10-06, 헤드리스(`multi-user.target`) |
| 커널 | 6.18.50+rpt-rpi-2712, aarch64, 64-bit userland |
| Python | 3.13.5 (`/usr/bin/python3`), pip3 없음, venv/ensurepip 사용 가능 |
| CPU | Cortex-A76 × 4, 1.5–2.4 GHz, L2 512 KiB × 4, L3 2 MiB, governor `ondemand` |
| MemTotal | 4,245,766,144 B (4,049.1 MiB) |
| Swap | zram 2 GiB (2,147,483,648 B), dphys-swapfile 비활성, swappiness 60 |
| 저장장치 | microSD 62,534,975,488 B, 루트 여유 56,293,048,320 B |
| 냉각 | `cooling_fan` 미감지 (팬 없음으로 판단) |
| 전원 | EXT5V 5.087 V, `get_throttled=0x0` (측정 시점) |
| 네트워크 | Wi-Fi(wlan0)만 사용 |
| Docker | 미설치 |
| cgroup | v2, 컨트롤러 `cpuset cpu io pids` — **memory 없음** (커널 cmdline `cgroup_disable=memory`) |

## Idle 메모리 (Docker 없음)

| 지표 | 바이트 | MiB |
|---|---|---|
| 호스트 사용량 (`MemTotal − MemAvailable`) 최소 | 243,744,768 | 232.5 |
| 호스트 사용량 평균 | 251,225,156 | 239.6 |
| 호스트 사용량 최대 | 274,907,136 | 262.2 |
| MemAvailable 최소 | 3,970,859,008 | 3,786.9 |
| 3,500,000,000 B 목표까지 여유 (최대 기준) | 3,225,092,864 | — |
| Swap 사용 최대 | 0 | 0 |
| CPU 온도 | 39.15 / 40.6 / 41.9 °C (최소/평균/최대) | |

## 판정 및 영향

- 이 환경에서 OS 단독 상주 메모리는 0.25 GB 수준이다. Docker 데몬의 메모리 오버헤드는 **미측정**(미설치).
- **메모리 cgroup 비활성**: 이 상태에서는 Docker `mem_limit`/`memswap_limit`와 systemd `MemoryMax=`가 적용되지 않고,
  컨테이너·서비스별 `memory.current`/`memory.peak`/OOM 이벤트를 측정할 수 없다.
  SYS-MEM-03/04/06 검증 전에 활성화(`cgroup_enable=memory` + 재부팅)가 필요하다 — 시스템 변경이므로 승인 대기.
- 호스트 전체 기준(SYS-MEM-01) 측정은 `/proc/meminfo`로 현재 상태에서도 가능하다.
- 팬 미감지: 부하 테스트에서 열 스로틀링 위험 → [성능 보고서](../../../performance/results/20261102-pi5-native-bench/report.md)에서 실제로 발생.
