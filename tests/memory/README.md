# tests/memory/ — 메모리 안정성 테스트

담당: **D** (B·C 지원) · 상태: 계획 (도구·결과 없음)

> 목표: 호스트 전체 메모리(`MemTotal − MemAvailable`) **< 3,500,000,000 바이트**, OOM 0건.
> 예산과 측정 정의: [docs/memory-budget.md](../../docs/memory-budget.md)

## 테스트 (구현 예정)

| ID | 시나리오 | 기간 | 판정 데이터 |
|---|---|---|---|
| MT-01 | 유휴(입력 없음) 기준선 | 10분 | 호스트 사용량, 컨테이너별 `memory.current` |
| MT-02 | 목표 FPS 단기 | 10~15분 | 호스트 최대, 컨테이너 피크, `memory.stat` 구성 |
| MT-03 | 목표 FPS 장시간 | **60분 이상** | 호스트 최대, 웜업 후 증가 기울기, OOM, 재시작 |
| MT-04 | 과부하(150% FPS, 다수 인원) | 15분 | 큐 상한 하에서 메모리 상한 유지 |
| MT-05 | WAN 60분 단절 후 백로그 업로드 | 60분 + 복구 | cloud-sync 피크(< 192 MiB), 디스크 증가량 |
| MT-06 | 컨테이너 재시작 반복 | 10회 | 재시작 후 메모리 복귀 수준 |
| MT-07 | Docker vs native 비교 | MT-02/03 동일 조건 | 호스트 사용량 차이 |

## 수집 항목

- `/proc/meminfo` 전체(1초), cgroup `memory.current`, `memory.peak`, `memory.stat`, `memory.events`, `memory.swap.current`
- `/proc/pressure/memory`, `/proc/vmstat`의 swap in/out
- 커널 로그의 OOM 메시지
- 측정 도구 자체의 사용량(호스트 사용량에 포함됨을 명시)

결과 보관 규칙은 [tests/performance/README.md](../performance/README.md)와 같다(`tests/memory/results/<YYYYMMDD>-<시나리오>/`).
