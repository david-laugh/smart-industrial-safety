# 통합 보고서: XIAO → Pi 5 → Supabase → 대시보드 전 구간 (2026-11-02)

- 범위: 인터페이스 1~5 + 대시보드 조회 (I-4, I-5, I-6). 앞 구간(XIAO → Pi 이벤트)은
  [20261102-xiao-pi-e2e](../20261102-xiao-pi-e2e/report.md) 참고
- 기간: cloud-sync 첫 업로드 15:47 KST, 실시간 관측 15:48 ~ 16:08 KST (06:48 ~ 07:08 UTC)
- 관측 방법: Supabase `ppe_events` 3초 간격 조회(읽기 전용), Pi `cloud_sync status`, Gateway `/healthz`, 로그
- secret 키는 값 출력 없이 파일(0600)로만 다뤘다. 증거 이미지는 저장소에 넣지 않았다.

## 구성

| 구간 | 내용 |
|---|---|
| ESP32 | XIAO ESP32S3 Sense, `frame_uploader` v0.2.0, FOMO 사람 감지, `device_id=xiao-cam-01` |
| Pi Gateway | `~/ppe-gw` (master `6e7571c`, gateway/inference/ppe_decision 은 `4d1624a` 와 동일), `0.0.0.0:8080`, `ORT_INTRA_OP_THREADS=2` |
| Pi cloud-sync | `edge/cloud_sync` + `edge/event_store` (master `4d1624a`), 상주 실행, `SYNC_POLL_INTERVAL_S=2`(기본) |
| 키 | `~/ppe-gw/secrets/supabase_service_key`(0600, `sb_secret_`), URL 은 `secrets/cloud_sync.env`(0600) |
| Supabase | dev 프로젝트, `dashboard/seed/schema.sql` 적용: `public.ppe_events`, 비공개 버킷 `ppe-evidence`(1 MiB, `image/jpeg`) |
| 대시보드 | PC, `python -m server`, `DASHBOARD_DATA_SOURCE=supabase`, `http://127.0.0.1:8765` (`/healthz` → `data_source: supabase`) |

배치 전 처리: Pi spool 의 로컬 curl 테스트 이벤트 2건(`cam-line-a-01`)은 seed 가데이터와 장치 이름이 겹쳐
`~/ppe-gw/data_localtest/` 로 옮겨 업로드에서 제외했다.

## 결과

### 업로드

| 항목 | 값 |
|---|---|
| 기존 적체 이벤트 업로드(`cloud_sync once`) | 7건 ingest → 7건 SYNCED, 재시도·DEAD_LETTER·거부 0 |
| 실시간 업로드(상주) | 18건 |
| 최종 outbox | `SYNCED 25`, PENDING·IMAGE_UPLOADED·DEAD_LETTER 0 |
| Supabase `ppe_events` (`xiao-cam-01`) | **25행** = Pi 판정 이벤트 25건 (중복 0, 누락 0) |
| Storage 경로 | `xiao-cam-01/2026/11/02/{event_id}.jpg` |
| cloud-sync / Gateway 로그 경고·오류 | 0 / 0 |
| 대시보드 | 목록·상세에 이벤트와 증거 이미지 표시 (사용자 브라우저 확인) |

### 지연 (실시간 18건)

| 구간 | min / median / max (s) | 비고 |
|---|---|---|
| Pi 판정 → Supabase 행 (`ingested_at − decided_at`) | 1.0 / 2.4 / 4.1 | cloud-sync 폴링 2 s + Storage 업로드 + 행 upsert. Pi 시계와 Supabase 서버 시계 차 포함 |
| 촬영 → Supabase 행 (`ingested_at − occurred_at`) | 1.9 / 3.0 / 4.7 | ESP32 SNTP 시계 기준 |

대시보드 화면 반영은 브라우저 새로고침 또는 실시간 토글 주기에 따라 추가된다(미측정).

### 수신·판정 (Gateway, 16:08 KST)

| 항목 | 값 |
|---|---|
| accepted / processed | 4,669 / 4,669 |
| rejected / dropped / failed | 0 / 0 / 0 |
| events (Gateway 기동 후) | 25 |

### 이벤트 구성과 중복 억제 동작 (실시간 18건)

| 위반 | 건수 |
|---|---|
| HELMET_MISSING | 14 |
| VEST_MISSING | 3 |
| HELMET_MISSING + VEST_MISSING | 1 |

- **같은 격자 칸에서는 30초 억제가 동작**: `pos:r1c2` 06:50:51 → 06:51:22 (30.1 s), `pos:r1c1` 06:57:09 → 06:57:46 (36.7 s)
- **짧은 간격 연속 이벤트는 모두 칸이 다름**: 예) 07:06:07 `r1c2`, 07:06:09 `r1c1`, 07:06:10 `r1c3` (사람 박스 중심 x 0.54 → 0.42 → 0.68)
- 원인: 추적기가 없어 사람이 4×4 격자 칸을 넘나들면 다른 사람으로 보고 새 `dedup_key` 를 만든다
  (`edge/ppe_decision/README.md` 남은 이슈 "추적기 부재"). 관측 장면은 1명이 움직이는 상황으로 추정 → **과다 이벤트 경향**.

## 판정

| 항목 | 결과 |
|---|---|
| I-4 cloud-sync → Supabase (Storage → 행, `event_id` 멱등) | **통과** (25/25, 중복 0) |
| I-5 Supabase → 대시보드 (secret 키 서버 조회, Signed URL 이미지) | **통과** (사용자 화면 확인) |
| I-6 전 구간 XIAO → 대시보드 | **통과**, 촬영 → Supabase median 3.0 s |
| SYS-REL-02 재시도·재시작 후 중복 0 | 부분 확인(정상 경로에서 중복 0). 강제 재시작 시나리오는 미실시 |

## 미검증

- SYS-REL-01 WAN 10분 단절 후 유실 0, cloud-sync 강제 종료·재시작, 401 시 일시 중지(장애 테스트)
- cloud-sync 메모리 피크(192 MiB 상한 대비), 장시간 백로그
- 대시보드 비인증 접근 차단(SYS-SEC-01) — 이번 설정은 `DASHBOARD_BIND_ADDR=0.0.0.0`, 접근 토큰 없음(내부망 전제)
- 대시보드 화면 반영 지연(새로고침/실시간 토글 주기)
- Pi 프로세스는 nohup 수동 실행 → 재부팅 시 종료(systemd 미구성)

## 후속 조치

| 담당 | 내용 |
|---|---|
| B | 격자 칸 기반 dedup 의 과다 이벤트: 간단한 추적기(IoU 연결) 또는 장치·위반 유형 단위 억제 옵션 검토 |
| C(통합) | 장애 테스트(SYS-REL-01/02), 대시보드 접근 토큰 또는 바인드 주소 제한, `schema.sql` → `cloud/` 정식 마이그레이션 |
| D | Pi systemd 서비스 2개(edge-core, cloud-sync), `.env.example` 에 B·C 추가 변수 반영, cloud-sync 메모리 측정 |
