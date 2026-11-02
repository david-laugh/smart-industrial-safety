# 수용 기준

> 판정은 Raspberry Pi 5 (4GB) ARM64 실기 측정으로만 한다.
> "확정" 기준은 프로젝트 제약에서 나온 고정 기준이고, "TBD" 기준은 첫 기준선 측정 후 팀 합의로 숫자를 정한다. TBD 값을 측정 전에 추정해 채우지 않는다.

## 1. 시스템 수준

| ID | 기준 | 값 | 상태 | 검증 방법 |
|---|---|---|---|---|
| SYS-MEM-01 | 호스트 메모리(`MemTotal − MemAvailable`) 최대값 | < 3,500,000,000 B | 확정 | 60분 이상 장시간 테스트 |
| SYS-MEM-02 | OOM kill (커널/cgroup) | 0건 | 확정 | 단기·장시간·장애 테스트 전체 |
| SYS-MEM-03 | `edge-core` 메모리 피크 | < 1900 MiB (상한 도달 없이 여유 확보) | 확정(상한), 여유율 TBD | cgroup `memory.peak` |
| SYS-MEM-04 | `cloud-sync` 메모리 피크 | < 192 MiB, 60분 백로그 업로드 포함 | 확정(상한), 여유율 TBD | cgroup |
| SYS-MEM-05 | 웜업 이후 메모리 증가 기울기 | TBD 바이트/시간 이하 | TBD | 장시간 테스트 선형 회귀 |
| SYS-MEM-06 | 컨테이너 swap 사용 | 0 (`memswap_limit = mem_limit`) | 확정 | cgroup `memory.swap.current` |
| SYS-PERF-01 | 목표 입력 FPS(장치당/전체) | TBD | TBD | 단기 테스트 |
| SYS-PERF-02 | Pi 내부 지연 T1→T5 p95 | TBD ms | TBD | 단기·장시간 테스트 |
| SYS-PERF-03 | End-to-End T0→T5 p95 (시계 오차 함께 보고) | TBD ms | TBD | 단기 테스트 |
| SYS-PERF-04 | 목표 FPS에서 프레임 드롭률 | TBD % | TBD | 단기 테스트 |
| SYS-PERF-05 | 큐 길이가 상한에 머무르는 시간 비율(목표 FPS) | TBD % | TBD | 단기 테스트 |
| SYS-REL-01 | WAN 10분 단절 후 이벤트 유실 | 0건 | 확정 | 장애 테스트 |
| SYS-REL-02 | 재시도/재시작 후 Supabase 중복 행·객체 | 0건 | 확정 | 장애 테스트 |
| SYS-REL-03 | 복구 후 백로그 업로드 완료 시간 | TBD | TBD | 장애 테스트 |
| SYS-REL-04 | 전원 차단 후 로컬 저장소 손상 | 0건 | 확정 | 전원 차단 테스트 |
| SYS-REL-05 | ESP32 Wi-Fi 복구 후 자동 재전송 | 사람 개입 없음 | 확정 | 장애 테스트 |
| SYS-SEC-01 | 비인증 사용자의 이벤트/이미지 접근 | 불가 | 확정 | 대시보드·Storage 접근 테스트 |
| SYS-SEC-02 | 저장소에 비밀 정보 커밋 | 0건 | 확정 | 리뷰 + 비밀 스캔 |

## 2. 계약 수준

| ID | 기준 | 상태 |
|---|---|---|
| CON-01 | 모든 생산자 출력 샘플이 해당 JSON Schema 통과 (100%) | 확정 |
| CON-02 | 모든 PPE 상태 값은 `WORN`/`MISSING`/`UNKNOWN` | 확정 |
| CON-03 | 모든 시각은 `Z` 접미사 UTC | 확정 |
| CON-04 | 이미지 바이트가 JSON(Base64)에 포함된 메시지 0건 | 확정 |
| CON-05 | 같은 이벤트의 모든 재전송에서 `event_id` 동일 | 확정 |
| CON-06 | `mcu_uptime_ms`와 `gateway_received_at_utc`가 모든 이벤트에 분리 기록 | 확정 |

## 3. 컴포넌트 수준

### Firmware (A)
| ID | 기준 | 상태 |
|---|---|---|
| FW-01 | 30분 연속 동작 중 예기치 않은 리셋 0회 | 확정 |
| FW-02 | 재부팅 시 `boot_id` 변경, `frame_seq` 초기화 | 확정 |
| FW-03 | 429 응답 시 `retry_after_ms` 준수 | 확정 |
| FW-04 | 목표 해상도·품질에서 전송 FPS | TBD |
| FW-05 | (선택) Edge Impulse 사람 감지 사용 시 감지 지연·정확도 보고 | 보고 필수, 기준 TBD |

### Inference / Decision (B)
| ID | 기준 | 상태 |
|---|---|---|
| AI-01 | 평가 세트에서 helmet/vest 클래스별 정밀도·재현율 | TBD (평가 세트 확정 후) |
| AI-02 | 미착용 이벤트 재현율(사람 단위) | TBD |
| AI-03 | 오경보율(착용자를 MISSING으로 판정) | TBD |
| AI-04 | 추론 지연 p95 (Pi 5 실기) | TBD |
| AI-05 | UNKNOWN만 있는 사람은 기본 정책에서 이벤트 0건 | 확정 |
| AI-06 | `dedup_window_s` 내 같은 사람·같은 위반 이벤트 1건 | 확정 |
| AI-07 | 모델 카드(데이터셋, 클래스, 입력 크기, 라이선스, sha256) 존재 | 확정 |

### Edge / Cloud (C)
| ID | 기준 | 상태 |
|---|---|---|
| EC-01 | 잘못된 요청에 계약된 HTTP 코드·오류 코드로 응답 | 확정 |
| EC-02 | 큐 상한 초과 시 오래된 프레임 드롭 + 카운터 증가 | 확정 |
| EC-03 | 업로드 순서 Storage → DB (이미지 없는 행 0건) | 확정 |
| EC-04 | 401/403 시 업로드 중지 + 경보, 이벤트 보존 | 확정 |
| EC-05 | Storage 버킷 비공개, Signed URL 만료 적용 | 확정 |
| EC-06 | 로컬 디스크 상한 정책 동작(SYNCED부터 삭제, 미전송 보존) | 확정 |

### Infrastructure (D)
| ID | 기준 | 상태 |
|---|---|---|
| INF-01 | Compose 템플릿에 모든 서비스 `mem_limit`, `memswap_limit`, 로그 크기 제한 명시 | 확정 |
| INF-02 | systemd 설계에 `MemoryMax`, `MemorySwapMax=0`, 재시작 정책 명시 | 확정 |
| INF-03 | Docker vs native 비교 보고서 | 확정 |
| INF-04 | 측정 보고서에 환경 정보(benchmark-plan §2) 전체 기록 | 확정 |

## 4. 판정 절차

1. 각 역할이 컴포넌트 기준 자체 점검 → PR에 결과 첨부
2. D가 계약 테스트와 통합 테스트 실행
3. 기준선 측정 후 TBD 값을 팀 합의로 확정하고 이 문서 갱신(근거 보고서 링크)
4. 시스템 수준 기준 전체 PASS 시 릴리스 후보 태그
