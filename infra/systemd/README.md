# infra/systemd/ — native(비 Docker) 배포 대안

담당: **D (Infrastructure / QA / Integration)**

> 이 디렉터리에는 아직 unit 파일이 없다. 아래 unit 내용은 **설계 예시 텍스트**이며 구현 단계에서 실제 파일로 만든다.

## 목적

Docker 데몬(dockerd, containerd, shim)의 상주 메모리와 운영 복잡도를 없애고, 같은 메모리 상한을 systemd(cgroup v2)로 적용한다. Docker 배포와의 비교는 [docs/benchmark-plan.md](../../docs/benchmark-plan.md) §8.

## 구성(설계)

| 서비스 | 대응 컨테이너 | 실행 사용자 | 메모리 상한 |
|---|---|---|---|
| `ppe-edge-core.service` | edge-core | `ppe` (전용 시스템 사용자) | `MemoryMax=1900M` |
| `ppe-cloud-sync.service` | cloud-sync | `ppe` | `MemoryMax=192M` |
| `ppe.slice` | — | — | 두 서비스를 묶는 slice (선택) |

디렉터리 배치(설계):

| 경로 | 용도 |
|---|---|
| `/opt/ppe-safety/<버전>/` | 애플리케이션 코드 |
| `/opt/ppe-safety/venv/` | Python 가상환경 |
| `/opt/ppe-safety/models/` | 모델 가중치(읽기 전용) |
| `/etc/ppe-safety/ppe.env` | 환경변수(`.env.example`과 같은 변수명, 권한 0600) |
| `/var/lib/ppe-safety/` | `EVENT_DATA_ROOT` (spool, images, outbox.db) |

## unit 설계 예시 (텍스트)

```ini
# ppe-edge-core.service (예시 — 아직 파일 아님)
[Unit]
Description=PPE Safety edge-core (gateway + inference + decision)
After=network-online.target time-sync.target
Wants=network-online.target

[Service]
Type=simple
User=ppe
Group=ppe
EnvironmentFile=/etc/ppe-safety/ppe.env
ExecStart=<구현 단계에서 정의>
Restart=on-failure
RestartSec=5
MemoryMax=1900M
MemorySwapMax=0
# MemoryHigh=<측정 후 결정: MemoryMax보다 약간 낮게 두어 조기 회수 유도>
TasksMax=256
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/ppe-safety

[Install]
WantedBy=multi-user.target
```

`ppe-cloud-sync.service`는 같은 형식에 `MemoryMax=192M`, `TasksMax=64`, `After=network-online.target`을 사용한다.

## 운영 확인 항목(설계)

- 메모리 사용량: `systemctl show <서비스> -p MemoryCurrent` (`MemoryPeak` 속성은 최신 systemd에서만 제공 — OS 버전 확인) 및 `/sys/fs/cgroup/system.slice/<서비스>/memory.current`, `memory.peak`
- OOM: `journalctl -k` 의 OOM 메시지, `memory.events`
- 시계: `time-sync.target` 이후 기동해 `gateway_clock_synced` 정확도 확보
- 로그: journald 크기 제한(`SystemMaxUse`) 설정

## Docker 대비 트레이드오프

| 장점 | 단점 |
|---|---|
| 데몬 상주 메모리 없음 | Python·시스템 라이브러리 버전 관리 직접 수행 |
| systemd 의존성/시계 동기화 순서 제어가 쉬움 | 롤백을 위한 버전 디렉터리 관리 필요 |
| 디버깅 시 경로 단순 | 3-컨테이너 수준의 격리를 원하면 서비스 3개로 분리 필요 |

## 구현 단계 TODO (D)

- [ ] 실제 unit 파일 작성 및 `systemd-analyze verify` 확인
- [ ] 설치/업데이트/롤백 절차 문서화
- [ ] Docker 배포와 같은 조건 비교 측정
