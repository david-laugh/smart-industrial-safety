# dashboard/ — 이벤트 확인 웹 대시보드

담당: **C (Edge / Cloud)** · 상태: 구현 전 (소스 없음)

## 범위

- 로그인(인증 사용자만)
- 이벤트 목록: 기간, 장치, 위반 유형 필터, `occurred_at` 내림차순
- 이벤트 상세: 증거 이미지(Signed URL), bbox 오버레이, 사람별 helmet/vest 상태와 신뢰도, 시간 정보(`occurred_at_source` 표시)
- 읽기 전용 (이벤트 수정/삭제 기능 없음)

## 데이터 소스

| 데이터 | 출처 | 계약 |
|---|---|---|
| 이벤트 행 | Supabase `ppe_events` (select) | [`supabase-event-record`](../contracts/supabase-event-record.schema.json) |
| 이미지 | Storage `ppe-evidence` Signed URL | [cloud/README.md](../cloud/README.md) §2 |

## 규칙

- 클라이언트에는 **anon key만** 사용한다. service_role 키를 대시보드에 넣지 않는다.
- 시각은 UTC로 저장되어 있으므로 표시할 때만 현지 시간대로 변환한다.
- bbox는 0~1 정규화 좌표이므로 이미지 실제 크기에 곱해 그린다.

## 설계 결정 필요 (C)

- 프레임워크/호스팅 방식, 실시간 갱신(Realtime 구독) 사용 여부, 계정 관리 방식

## 환경변수 (구현 단계에서 템플릿 추가)

`SUPABASE_URL`, `SUPABASE_ANON_KEY` — 값은 커밋하지 않는다.
