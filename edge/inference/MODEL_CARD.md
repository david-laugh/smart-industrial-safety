# 모델 카드: yolov8n-ppe-hansung-cho (ONNX, 640)

> 이 문서의 수치는 **개발 PC(x86)와 테스트 영상 1개**로 얻은 값이다. Raspberry Pi 5 결과도, 일반화 성능도 아니다.

## 1. 출처

| 항목 | 값 |
|---|---|
| 원본 | Hugging Face [`Hansung-Cho/yolov8-ppe-detection`](https://huggingface.co/Hansung-Cho/yolov8-ppe-detection), 파일 `best.pt`, 저장소 커밋 `ac0027bd38bc619d5ce4f52b4cc01beb87d8b958` (2025-11-27) |
| 원본 sha256 | `2419700bbe3b8d38f9000655d9cf952a4bc93ef6c143baf8b49a0abde5d0760f` (6,250,090 bytes) |
| 구조 | YOLOv8n (ultralytics 8.3.232 로 학습), 파라미터 3.0M, 2.0 GFLOPs(640) |
| 학습 | `yolov8n.pt` 에서 fine-tuning, imgsz 640, 30 epoch (체크포인트 `train_args`) |
| 제공 | 강사 제공 모델(팀 선정) |
| **라이선스** | **HF 카드 MIT / 체크포인트 메타데이터 AGPL-3.0(Ultralytics), 불일치.** YOLOv8 코드·가중치 파생물은 Ultralytics AGPL-3.0 적용 가능성이 있으므로 배포·상용 사용 전 확인 필요 |
| **데이터셋** | **불명확** — 카드에 "Public PPE / construction-related datasets (Kaggle 등)"라고만 표기. 데이터 자체의 라이선스는 "데이터 제공처를 따름" |
| 카드 공개 지표 | Validation mAP@0.5 0.744, mAP@0.5:0.95 0.436, P 0.831, R 0.685 (작성자 보고값, 검증 세트 비공개) |

## 2. 클래스

모델 출력 10클래스(ONNX 메타데이터 `names`):

| ID | 이름 | 사용 | 역할(role) |
|---|---|---|---|
| 0 | Hardhat | ✅ | `helmet` (착용) |
| 1 | Mask | ✗ 후처리에서 버림 | |
| 2 | NO-Hardhat | ✅ | `no_helmet` (미착용) |
| 3 | NO-Mask | ✗ | |
| 4 | NO-Safety Vest | ✅ | `no_vest` (미착용) |
| 5 | Person | ✅ | `person` |
| 6 | Safety Cone | ✗ | |
| 7 | Safety Vest | ✅ | `vest` (착용) |
| 8 | machinery | ✗ | |
| 9 | vehicle | ✗ | |

클래스 ID 는 코드에 하드코딩하지 않는다. 런타임이 ONNX 메타데이터의 이름으로 ID 를 찾는다(`INFERENCE_CLASS_MAP` 으로 이름 변경 가능).

## 3. 변환(ONNX)

```bash
python edge/inference/tools/export_onnx.py --model edge/inference/models/best.pt --imgsz 640 416 320
```

ultralytics 8.4.198 export: `format=onnx, opset=12, simplify=True(onnxslim 0.1.98), dynamic=False, half=False, nms=False`, onnx 1.23.2.

| 파일 | 입력 | 출력 | sha256 | 크기(bytes) |
|---|---|---|---|---|
| **`ppe_yolov8n_640.onnx` (채택)** | 1x3x640x640 | 1x14x8400 | `846b747b01505804e384c16b5136b55754a9e64ec2de5f1fe2d32f3ffee64721` | 12,272,947 |
| `ppe_yolov8n_416.onnx` | 1x3x416x416 | 1x14x3549 | `1aa283f48042480d480b492a2e1da5ae6ebe7213c5c322e189881487a170f87c` | 12,175,849 |
| `ppe_yolov8n_320.onnx` | 1x3x320x320 | 1x14x2100 | `df653b6fd0371684f9712ad21e527e00f7b5281be5a1da2adb7bedf196a1f438` | 12,146,865 |

출력 `(1, 4+nc, N)`: `cx, cy, w, h`(레터박스 입력 픽셀) + 클래스별 점수(sigmoid 적용됨), NMS 미포함.
ultralytics/onnx 버전이 달라지면 같은 `.pt` 에서도 sha256 이 달라질 수 있다 → 배포 기준은 위 640 파일.

### 동등성 검증 (`tools/compare_pt_onnx.py`, 테스트 영상 20프레임)

| 입력 | PyTorch vs ORT 원시 출력 최대차 | ultralytics predict vs 런타임 PPEDetector 최종 검출 |
|---|---|---|
| 원본 1280x720 | 박스 0.0018 px, 점수 6e-6 | 418/418 일치, 최소 IoU 0.99999, 점수차 ≤ 2e-6 |
| ESP32 모사 640x480 JPEG q40 | 박스 0.0020 px, 점수 1.1e-5 | 404/404 일치, 최소 IoU 0.99999, 점수차 ≤ 2e-6 |

전처리: ultralytics `LetterBox(auto=False)` 와 동일(비율 유지, INTER_LINEAR, 패딩 114, 중앙 배치, `round(d±0.1)` 분할), BGR→RGB, /255.
후처리: 앵커별 최고 점수 클래스 → 점수 > 0.25 → **클래스별 NMS**(IoU 0.7, Hardhat/NO-Hardhat 이 서로 지우지 않음) → 레터박스 역변환 → 원본 기준 0~1 정규화.

### 입력 크기 비교 (640 vs 416/320, ESP32 모사 20프레임)

| | 640 | 416 | 320 |
|---|---|---|---|
| NO-Hardhat 점수 변화(640 대비, p50 / 최소) | — | −0.22 / −0.50 | −0.21 / −0.44 |
| 미착용자 프레임별 NO-Hardhat 최고점 p50 | 0.80 | 0.60 | 0.60 |
| 원경 인물(46~132 px) | 검출 | 대부분 놓침 | 대부분 놓침 |
| x86 추론 p50 (intra=3, 참고값) | 51 ms | 19 ms | 11 ms |

학습 imgsz 가 640 이라 416/320 은 NO-Hardhat 점수가 크게 떨어진다 → **640 채택**(Pi 5 지연 측정 후 재검토).

## 4. 사람-PPE 연관과 판정 (런타임 `association.py`)

| 파라미터 | 기본값 | 환경변수 |
|---|---|---|
| 안전모 영역 | 사람 박스 높이 상단 0~25% (가로 전체) | `INFERENCE_HELMET_REGION` |
| 조끼 영역 | 사람 박스 높이 20~70% | `INFERENCE_VEST_REGION` |
| 연관 조건 | PPE 박스 중심이 영역 안 AND IoA(PPE∩영역 / PPE 면적) ≥ 0.5 | `INFERENCE_ASSOC_MIN_IOA` |
| PPE 할당 | 판정 가능한 사람 중 IoA 가 가장 큰 1명에게만 | — |
| 사람 신뢰도 하한 | 0.5 | `INFERENCE_PERSON_MIN_CONFIDENCE` |
| 착용 / 미착용 임계값 | 0.5 / 0.5 | `INFERENCE_PPE_WORN_MIN_CONFIDENCE` / `..._MISSING_...` |
| 최소 사람 박스 높이 | 이미지 높이 × 0.25 (px 설정 시 px 우선) | `INFERENCE_MIN_PERSON_BOX_RATIO` / `..._PX` |
| 잘림 판정 여유 | 경계에서 2 px | `INFERENCE_EDGE_MARGIN_PX` |

상태 결정(항목별):
1. 사람 신뢰도 또는 박스 높이가 기준 미만 → helmet/vest 모두 `UNKNOWN`, confidence null.
2. 착용·미착용 둘 다 임계값 이상 → 점수 높은 쪽(완전 동률은 `UNKNOWN`).
3. 하나만 임계값 이상 → 그 상태, confidence = 그 점수.
4. 임계값 미만 착용 검출만 있음 → `UNKNOWN`(애매).
5. 검출 없음 → 부위가 잘리지 않았으면 `MISSING`(confidence = 사람 신뢰도), 잘렸으면 `UNKNOWN`.
   helmet 은 위/좌/우 잘림, vest 는 아래/좌/우 잘림을 본다(위쪽만 잘린 사람의 vest 는 판정).

임계값 근거(ESP32 모사, 640): 주 작업자 Hardhat ≥0.70, Safety Vest ≥0.85, 미착용자 프레임별 NO-Hardhat 최고점 ≥0.55,
NO-Safety Vest 최고점 ≥0.58. 원경 인물에 붙은 노이즈 Safety Vest ≤0.61, NO-Safety Vest ≤0.57 → 박스 높이 기준으로 제외.
최소 박스: 0.25(=120 px @480). 3인 영상 원경 인물 0.10~0.275(0.25 적용 시 경계에서 잘린 원경 인물 3건이 WORN, MISSING 0건),
걸어오는 작업자 영상 주 작업자 최소 0.255 → 0.33 대비 helmet MISSING 재현율 0.50→0.76, vest WORN 0.72→0.98(정밀도 1.00 유지).
**모두 테스트 영상 1개 기준 잠정값이며 실제 ESP32 프레임으로 재조정해야 한다.**

## 5. 평가 세트와 결과

| 항목 | 내용 |
|---|---|
| 세트 1 | AI 생성 건설현장 영상(`ppe_safety_test_fixed.mp4`, 1280x720, 15 fps, 10초)에서 2 fps 로 20프레임 |
| 세트 2 | 걸어오는 작업자 영상(`Worker_walking_at_construction_site_f72b27d7.mp4`, 1280x720, 24 fps, 10초, 고정 카메라)에서 5 fps 로 50프레임. 주 작업자 1명(정답 helmet=MISSING, vest=WORN) + 뒤쪽 원경 작업자(평가 제외, 50/50 UNKNOWN) |
| 변형 | 원본 / ESP32 모사(640x480 리사이즈 + OpenCV JPEG 품질 40) |
| 구성 | 주 작업자 3명(착용+착용, 안전모 미착용, 조끼 미착용) + 원경 인물 1~3명 |
| 결과 1 | 주 작업자 3명의 helmet/vest 상태가 원본·ESP32 모사 모두 20/20 프레임에서 기대값과 일치, 원경 인물은 UNKNOWN |
| 결과 2 (ESP32 모사, 박스 0.25) | helmet MISSING 정밀도 1.00 / 재현율 0.76 (MISSING 38, UNKNOWN 9, WORN 3), vest WORN 1.00 / 0.98. 리플레이 2/5/24 fps 모두 HELMET_MISSING 1건 |
| 라벨 | 정식 라벨 CSV 미작성. `tools/eval_status.py --make-template` 로 템플릿 생성 후 사람이 채워 정밀도/재현율 산출 예정 |

**평가 한계**: 10초 고정 장면 2개, 프레임 간 상관이 매우 높음 → 실질 독립 표본이 적다. 실제 ESP32 카메라 화질·화각,
가림, 야간, 다양한 PPE 색상, 마스크/모자 등 혼동 요소는 평가되지 않았다. 위 결과를 정확도 지표로 인용하지 말 것.
`Hardhat` 은 원경 인물(작음)에서도 높은 점수(최대 0.91)로 검출되므로 작은 박스 판정 불가 규칙이 중요하다.

- **짧게 민 머리를 Hardhat 으로 오검출(최고 0.77), 임계값으로 분리 불가, 평활화로 이벤트 영향 없음.**
  (세트 2: helmet WORN 오판 3프레임 0.54/0.77/0.64, 임계값 미만 Hardhat 0.26~0.40 으로 UNKNOWN 9프레임.
  실제 안전모 착용자의 Hardhat 최저점 0.70 과 겹친다.)
- 세트 2 의 helmet MISSING 은 대부분 '검출 없음' 규칙에서 나온다(NO-Hardhat ≥0.5 는 50프레임 중 2프레임).
  원거리 판정은 미착용 클래스보다 Hardhat 미검출에 의존한다.

## 6. 재현

```bash
pip install -r edge/inference/requirements-dev.txt
python edge/inference/tools/inspect_model.py
python edge/inference/tools/extract_and_predict.py --video data/test_videos/ppe_safety_test_fixed.mp4 --out edge/inference/_dev/frames --max-frames 40
python edge/inference/tools/export_onnx.py
python edge/inference/tools/compare_pt_onnx.py
```
