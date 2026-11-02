# 통합 계획

## 1. 원칙

- **계약 먼저 고정, 구현은 병렬.** 각 역할은 `contracts/examples/`를 대체 입력으로 사용해 다른 역할을 기다리지 않는다.
- **양쪽 끝부터 짝 통합.** 인접한 두 컴포넌트를 먼저 붙이고, 마지막에 전체를 연결한다.
- **실기에서 판정.** 통합 완료 기준은 Raspberry Pi 5 + ESP32-S3 실기에서 확인한다.

## 2. 마일스톤

| 단계 | 이름 | 내용 | 완료 기준 | 주 담당 |
|---|---|---|---|---|
| M0 | 계약 고정 | 스키마 v1.0.0 리뷰, 예시 확정 | 승인자 전원 리뷰 완료, 계약 테스트 계획 확정 | D (전원 리뷰) |
| M1 | 컴포넌트 단독 | 각 컴포넌트가 예시 입력으로 계약 출력 생성 | 각 역할의 출력 샘플이 스키마 통과 | A, B, C |
| M2 | 짝 통합 | 아래 §3의 짝별 통합 | 짝별 통합 테스트 통과 | 짝 담당 + D |
| M3 | 엔드투엔드 | ESP32 → Pi → Supabase → 대시보드 | 미착용 장면에서 이벤트가 대시보드에 표시 | D |
| M4 | 성능·메모리 | 단기/장시간/장애 테스트 | 시스템 수준 확정 기준 PASS, TBD 기준 숫자 확정 | D (전원 지원) |
| M5 | 배포 방식 결정 | Docker 2/3-컨테이너 vs native 비교 | 비교 보고서 + 표준 배포 결정 기록 | D |

## 3. 짝 통합 순서

| 순서 | 짝 | 인터페이스 | 검증 내용 | 대체 입력/출력 |
|---|---|---|---|---|
| I-1 | A ↔ C(Gateway) | 1 | multipart 수신, 오류 코드, 백오프, 시간 필드 | Gateway 전: 응답 예시로 펌웨어 응답 파서 확인 |
| I-2 | C(Gateway) ↔ B(Inference) | 2 | edge-frame 전달, 큐/드롭, JPEG 무결성(sha256) | Gateway 전: 고정 JPEG + `edge-frame.example.json` |
| I-3 | B(Decision) ↔ C(Event Store) | 4 | spool 원자 게시, 스키마 검증, outbox 적재 | Decision 전: `ppe-event.example.json` + 임의 JPEG |
| I-4 | C(Cloud Sync) ↔ Supabase | 5 | 멱등 upsert, Storage 업로드, 재시도 | 개발용 Supabase 프로젝트(운영과 분리) |
| I-5 | Supabase ↔ Dashboard | — | RLS, Signed URL, 목록/필터 | `supabase-event-record.example.json` 형태 시드 데이터 |
| I-6 | 전체 | 1~5 | M3 엔드투엔드 | — |

I-1, I-2, I-3, I-4/I-5는 서로 독립이므로 병렬로 진행할 수 있다.

## 4. 브랜치·리뷰 규칙 (제안)

- 기본 브랜치: `main` (보호). 작업 브랜치: `<역할>/<주제>` 예: `a/camera-init`, `b/onnx-session`, `c/outbox`, `d/compose`.
- PR 필수 리뷰: 해당 디렉터리 담당 역할 1인 + 계약/인프라 변경 시 D.
- 커밋 메시지에 관련 인터페이스 번호나 수용 기준 ID를 적는다(예: `CON-05`, `SYS-REL-02`).

## 5. 계약 변경 절차

1. 변경 제안 PR: 스키마 + 예시 + 이 문서들(protocols.md 등) 동시 수정, 변경 이유와 SemVer 영향(PATCH/MINOR/MAJOR) 명시.
2. 승인: [roles.md](roles.md) §인터페이스 소유권 표의 승인자 전원.
3. MINOR: 소비자가 새 필드를 무시/수용하도록 먼저 배포 → 생산자 배포.
4. MAJOR: 새 버전 스키마를 별도 `$id`로 추가, 소비자가 두 버전을 모두 받는 병행 기간 후 구버전 제거.
5. 계약 테스트 통과 후 병합.

## 6. 통합 환경

| 환경 | 구성 | 용도 |
|---|---|---|
| 개발 PC | x86 또는 ARM, 재생 입력 | 기능 개발, 계약 테스트 (성능 판정 불가) |
| 통합 Pi | Raspberry Pi 5 4GB, 실기 ESP32-S3 1대 | M2~M5, 모든 성능 판정 |
| Supabase dev | 개발 전용 프로젝트 | I-4, I-5, 장애 테스트 |
| Supabase prod | 운영 프로젝트 | 현장 배포 이후에만 사용 |

## 7. 위험과 완화

| 위험 | 영향 | 완화 |
|---|---|---|
| 모델이 Pi 5에서 목표 FPS를 못 냄 | SYS-PERF 미달 | 입력 해상도/모델 크기 단계별 측정, ESP32 사람 감지로 전송량 감소 |
| edge-core 메모리 1900m 초과 | OOM | 메모리 절감 레버(memory-budget.md §8), 3-컨테이너 분리 |
| SD카드 쓰기 마모/지연 | 저장 지연·손상 | 이벤트 이미지만 저장, SSD 권장, 쓰기량 측정 |
| ESP32 시계 오차 | 잘못된 occurred_at | 시간 규칙의 skew 임계값, GATEWAY_RECEIVED 대체 |
| 고권한 Supabase 키가 장치에 존재 | 유출 시 피해 | 파일 권한·secret, 키 회전 절차, 장치별 제한 자격증명 검토 |
| 열 스로틀링 | 지연 증가 | Active Cooler, 온도·스로틀 기록 |

## 8. 담당자별 첫 작업 (M0 → M1)

- **A**: 카메라 모듈/보드 확정(PSRAM), 해상도·품질 후보 정의, `esp32-frame-metadata` 리뷰, 펌웨어 구조 설계 문서화
- **B**: 모델 후보·데이터셋·라이선스 조사, 평가 세트 기준 정의, `edge-frame`/`inference-result`/`ppe-event` 리뷰, UNKNOWN·평활화 정책 문서화
- **C**: Gateway 엔드포인트·큐 설계, outbox 스키마(SQLite) 설계, Supabase 테이블·RLS·버킷 설계 확정, 대시보드 화면 정의
- **D**: Pi 5 기준 OS 설정 절차, 계약 테스트 도구 선정, 측정 수집 방법 설계, 재생 입력 방식 설계, M0 리뷰 주관
