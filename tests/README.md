# tests/ — 테스트 계획

담당: **D (Infrastructure / QA / Integration)** · 상태: 계획만 존재 (테스트 코드 없음)

| 디렉터리 | 목적 | 실행 환경 |
|---|---|---|
| [`contract/`](contract/README.md) | 모든 메시지가 `contracts/` 스키마를 지키는지 | 개발 PC / CI |
| [`integration/`](integration/README.md) | 짝 통합 및 엔드투엔드 동작 | 통합 Pi + ESP32 실기 |
| [`performance/`](performance/README.md) | FPS, 지연, CPU, 드롭, 큐 | **Raspberry Pi 5 실기 ARM64** |
| [`memory/`](memory/README.md) | 호스트·컨테이너 메모리, 장시간 안정성, OOM | **Raspberry Pi 5 실기 ARM64** |

판정 기준: [docs/acceptance-criteria.md](../docs/acceptance-criteria.md) · 측정 방법: [docs/benchmark-plan.md](../docs/benchmark-plan.md)
