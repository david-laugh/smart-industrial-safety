# infra/docker/ — 컨테이너 배포 템플릿

담당: **D (Infrastructure / QA / Integration)**

> 이 디렉터리의 파일은 **비실행 템플릿**이다. Dockerfile·이미지가 없고, 파일명이 `compose.yaml`이 아니어서 `docker compose`가 자동으로 읽지 않는다. 빌드·실행은 구현 단계에서 한다.

## 파일

| 파일 | 내용 |
|---|---|
| `compose.template.yml` | 기본 배포: `edge-core`(1900m) + `cloud-sync`(192m) |
| `compose.split.template.yml` | 선택 배포: `gateway`(256m) + `inference`(1600m) + `cloud-sync`(192m) |
| `.env.example` | 환경변수 템플릿(비밀 값 비움). `.env`로 복사해 사용, 커밋 금지 |

## 설계 규칙

- 모든 서비스에 `mem_limit`과 같은 값의 `memswap_limit` → 컨테이너 swap 금지
- `platform: linux/arm64` 명시 (Raspberry Pi 5)
- `read_only: true` + 크기 제한 `tmpfs` (tmpfs는 컨테이너 메모리 상한에 과금됨)
- 로그 `json-file` `max-size 10m × 3` → 디스크 고갈 방지
- 이미지 태그는 고정 버전 사용(`latest` 금지), 베이스 이미지는 arm64 지원 확인
- Gateway 포트만 외부 노출, `GATEWAY_BIND_ADDR`로 현장 LAN 인터페이스에 바인딩
- 비밀 정보(장치 토큰, Supabase 키)는 Docker secret 또는 권한 0600 파일로 주입

## 구현 단계 TODO (D)

- [ ] `edge-core`, `cloud-sync` Dockerfile (멀티스테이지, arm64 슬림 베이스)
- [ ] 헬스체크 정의 (`/healthz`, cloud-sync 하트비트)
- [ ] 이미지 크기·기동 시간 기록
- [ ] 템플릿을 `compose.yaml`로 확정
- [ ] `docker info` 메모리 제한 지원 확인 절차 ([docs/memory-budget.md](../../docs/memory-budget.md) §7)

## 메모리 메모

`mem_limit`은 컨테이너 상한일 뿐이다. 판정은 호스트 전체 메모리(`MemTotal − MemAvailable`) < 3,500,000,000 B 이다. 자세한 내용: [docs/memory-budget.md](../../docs/memory-budget.md)
