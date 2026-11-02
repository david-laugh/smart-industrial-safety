# tests/performance/ — 성능 테스트

담당: **D** (B·C 지원) · 상태: 계획 (도구·결과 없음)

> 판정용 측정은 **Raspberry Pi 5 (4GB) 실기 ARM64**에서만 한다. 결과가 없는 상태에서 수치를 기록하지 않는다.

측정 계획 전체: [docs/benchmark-plan.md](../../docs/benchmark-plan.md)

## 범위

- 입력/처리 FPS, 디코드·전처리·추론·후처리 지연, Pi 내부 지연(T1→T5), End-to-End(T0→T5), 업로드 지연
- 컨테이너별 CPU 사용률, 프레임 드롭, 큐 길이, 온도·스로틀
- 10~15분 단기 테스트: 설정 조합 비교(FPS 단계 × 배포 방식 × ONNX 스레드 수)
- 네트워크 장애·복구 테스트(지연·백로그 처리 시간 측면)
- Docker(2/3-컨테이너) vs native 비교

## 결과 보관 규칙 (구현 단계)

```
tests/performance/results/<YYYYMMDD>-<시나리오>/
├── report.md     # benchmark-plan.md §9 템플릿 — 커밋
└── raw/          # CSV/JSONL 원시 데이터 — 커밋하지 않음(.gitignore)
```
