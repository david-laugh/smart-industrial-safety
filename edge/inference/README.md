# edge/inference/ — ONNX Runtime 추론

담당: **B (AI / Vision)** · 배포: `edge-core` (3-컨테이너 시 `inference`) · 상태: 구현 전

## 책임

- JPEG 디코드 → 리사이즈/레터박스 → 정규화 → ONNX Runtime(CPU EP, ARM64) 추론 → NMS
- 사람-PPE 연관(안전모: 사람 박스 상단 영역, 조끼: 몸통 영역)
- 사람별 `helmet`/`vest` 상태(`WORN`/`MISSING`/`UNKNOWN`)와 신뢰도
- 단계별 소요 시간(`timing`) 실측 기록

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | [`edge-frame`](../../contracts/edge-frame.schema.json) + JPEG 바이트 |
| 출력 | [`inference-result`](../../contracts/inference-result.schema.json) |

상태 정의: [docs/protocols.md §3](../../docs/protocols.md)

## 설계 메모

- InferenceSession은 프로세스 수명 동안 1개만 생성해 재사용한다.
- 스레드: `ORT_INTRA_OP_THREADS` 초기값 3(측정 대상), `ORT_INTER_OP_THREADS=1`.
- 모델 후보는 정확도·지연·메모리를 **Pi 5 실기에서** 측정해 결정한다. 개발 PC 결과로 결정하지 않는다.
- 모델 가중치는 저장소에 넣지 않는다 → [`models/README.md`](models/README.md)

## B가 남길 산출물 (구현 단계)

- 모델 카드: 데이터셋, 클래스 목록, 입력 크기, 라이선스, 학습/변환 절차, sha256
- 평가 세트 정의와 결과(클래스별 정밀도/재현율)
- Pi 5 지연·메모리 측정 결과([docs/benchmark-plan.md](../../docs/benchmark-plan.md))
