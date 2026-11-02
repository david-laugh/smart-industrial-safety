# infra/ — 배포 설계

담당: **D (Infrastructure / QA / Integration)**

| 디렉터리 | 내용 |
|---|---|
| [`docker/`](docker/README.md) | Docker Compose 비실행 템플릿(기본 2-컨테이너, 선택 3-컨테이너), `.env.example` |
| [`systemd/`](systemd/README.md) | Docker 없이 native/systemd로 배포하는 대안 설계 |

배포 방식 비교와 선택 기준: [docs/architecture.md §4](../docs/architecture.md), 메모리 예산: [docs/memory-budget.md](../docs/memory-budget.md)
