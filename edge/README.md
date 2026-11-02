# edge/ — Raspberry Pi 5 엣지 모듈

모든 모듈은 ARM64 Linux(Raspberry Pi 5, 4GB)에서 동작한다. 상태: 구현 전 (소스 없음)

| 모듈 | 담당 | 배포 단위(기본) | 입력 계약 | 출력 계약 |
|---|---|---|---|---|
| [`gateway/`](gateway/README.md) | C | edge-core | `esp32-frame-metadata` + JPEG | `gateway-response`, `edge-frame` |
| [`inference/`](inference/README.md) | B | edge-core | `edge-frame` | `inference-result` |
| [`ppe_decision/`](ppe_decision/README.md) | B | edge-core | `inference-result` | `ppe-event` + JPEG (spool) |
| [`event_store/`](event_store/README.md) | C | cloud-sync | `ppe-event` (spool) | outbox 레코드(내부) |
| [`cloud_sync/`](cloud_sync/README.md) | C | cloud-sync | outbox 레코드 | `supabase-event-record` + Storage 객체 |

배포 구성: [docs/architecture.md §4](../docs/architecture.md)
