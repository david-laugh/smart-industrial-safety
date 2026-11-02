# edge/inference/ — ONNX Runtime 추론

담당: **B (AI / Vision)** · 배포: `edge-core` (3-컨테이너 시 `inference`) · 상태: 구현됨(PC 검증, Pi 5 미측정)

> 통합 진입점과 인계 정보는 [`../ppe_decision/HANDOFF.md`](../ppe_decision/HANDOFF.md), 모델 정보는 [`MODEL_CARD.md`](MODEL_CARD.md).

## 책임

- JPEG 디코드 → 레터박스(640, 패딩 114) → 정규화 → ONNX Runtime(CPU EP) 추론 → 클래스별 NMS
- 사람-PPE 연관(안전모: 사람 박스 상단 0~25%, 조끼: 20~70%)
- 사람별 `helmet`/`vest` 상태(`WORN`/`MISSING`/`UNKNOWN`)와 신뢰도
- 단계별 소요 시간(`timing`)을 `time.perf_counter()` 로 실측 기록

## 인터페이스

| 방향 | 계약 |
|---|---|
| 입력 | [`edge-frame`](../../contracts/edge-frame.schema.json) + JPEG 바이트 |
| 출력 | [`inference-result`](../../contracts/inference-result.schema.json) |

```python
from edge.inference.api import infer, InferenceEngine
result = infer(edge_frame, jpeg)          # 모듈 싱글턴(환경변수로 첫 호출 시 생성)
engine = InferenceEngine()                # 또는 직접 생성해 재사용
result = engine.infer(edge_frame, jpeg)
```

보통은 추론과 판정을 묶은 `edge.ppe_decision.pipeline.PPEPipeline` 을 쓴다.

## 구성

| 파일 | 내용 |
|---|---|
| `config.py` | 환경변수(`INFERENCE_*`, `ORT_*`) 로드 + 기본값 |
| `detector.py` | `PPEDetector` — InferenceSession 1개 재사용, 레터박스, 클래스별 NMS, 좌표 역변환 |
| `association.py` | 사람-PPE 연관과 상태 판정(순수 함수) |
| `api.py` | `infer()` / `InferenceEngine` — inference-result 조립, 사람 박스 출처 선택 |
| `contract_check.py` | (개발/테스트) jsonschema + referencing 으로 `contracts/` 검증 |
| `tools/` | 개발 도구(ultralytics 사용 가능): `inspect_model`, `extract_and_predict`, `export_onnx`, `compare_pt_onnx`, `eval_status` / 런타임 전용: `bench_pi` |
| `tests/` | pytest (모델 파일이 없으면 모델 의존 테스트는 skip) |

런타임 의존성은 `numpy`, `opencv-python-headless`, `onnxruntime` 뿐이다(`requirements.txt`).
`ultralytics`/`torch` 는 `tools/` 의 개발 도구에서만 쓰며 런타임 코드는 import 하지 않는다(`requirements-dev.txt`).

## 사람 박스 출처 (`INFERENCE_PERSON_SOURCE`)

| 값 | 동작 |
|---|---|
| `auto`(기본) | 모델에 Person 클래스가 있으면 `model`, 없으면 `esp32` |
| `model` | 모델 Person 검출 사용 |
| `esp32` | `edge_frame["edge_detection"]["persons"]`(`[{bbox, score}]`, 스키마 1.1.0 제안) 사용. 없거나 null 이면 `persons=[]`. 퇴화 박스(FOMO 중심점)는 버림 |

현재 모델은 Person 클래스가 있으므로 `auto` = `model`. `persons` 필드는 스키마 1.1.0 반영 전이므로 방어적으로만 읽는다.

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `INFERENCE_MODEL_PATH` | `/models/` | 디렉터리면 `INFERENCE_MODEL_FILENAME` 을 붙임 |
| `INFERENCE_MODEL_FILENAME` | `ppe_yolov8n_640.onnx` | |
| `INFERENCE_MODEL_SHA256` | (없음) | 설정 시 로드 시점 검증 |
| `INFERENCE_MODEL_NAME` / `_VERSION` | `yolov8n-ppe-hansung-cho` / `hf-ac0027b-onnx-op12` | 결과 `model` 필드 |
| `ORT_INTRA_OP_THREADS` / `ORT_INTER_OP_THREADS` | 3 / 1 | |
| `INFERENCE_PERSON_SOURCE` | `auto` | `auto`/`model`/`esp32` (별칭 `PERSON_SOURCE`) |
| `INFERENCE_PERSON_MIN_CONFIDENCE` | 0.5 | |
| `INFERENCE_PPE_WORN_MIN_CONFIDENCE` | 0.5 | 미설정 시 `INFERENCE_PPE_MIN_CONFIDENCE` |
| `INFERENCE_PPE_MISSING_MIN_CONFIDENCE` | 0.5 | 미설정 시 `INFERENCE_PPE_MIN_CONFIDENCE` |
| `INFERENCE_MIN_PERSON_BOX_RATIO` | 0.25 | 이미지 높이 대비 최소 사람 박스 높이 |
| `INFERENCE_MIN_PERSON_BOX_PX` | (없음) | 설정하면 비율보다 우선 |
| `INFERENCE_DET_MIN_CONFIDENCE` / `INFERENCE_NMS_IOU` / `INFERENCE_MAX_DET` | 0.25 / 0.7 / 300 | 검출 후처리 |
| `INFERENCE_HELMET_REGION` / `INFERENCE_VEST_REGION` | `0.0,0.25` / `0.2,0.7` | 사람 박스 높이 대비 영역 |
| `INFERENCE_ASSOC_MIN_IOA` | 0.5 | |
| `INFERENCE_EDGE_MARGIN_PX` | 2 | 이미지 경계 잘림 판정 여유 |
| `INFERENCE_CLASS_MAP` | (없음) | `person=Person,helmet=Hardhat,...` 로 클래스 이름 매핑 변경 |

빈 문자열 값은 미설정으로 본다.

> **최소 박스 기준(0.25)과 임계값(0.5)은 테스트 영상 3개 기준 값이며 실제 ESP32 프레임으로 재조정이 필요하다.**
> **카메라 해상도·화질을 바꾸면 재검증 필요**: 원본 1280x720 에서는 형광 티셔츠의 조끼 미착용 이벤트를 놓쳤다(640x480 q40 에서는 검출, [MODEL_CARD](MODEL_CARD.md) §5).

## 개발 PC 에서 실행

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r edge/inference/requirements-dev.txt
# 모델: edge/inference/models/ppe_yolov8n_640.onnx 배치(HANDOFF.md §5) 또는 export_onnx.py 로 생성
python -m pytest edge/inference/tests edge/ppe_decision/tests -q
python edge/ppe_decision/tools/run_replay.py --loops 4
```

## Raspberry Pi 5 에서 실행·측정

대상: Raspberry Pi 5 4GB, Raspberry Pi OS 64-bit(Bookworm, Python 3.11), 쿨러 장착.

### 1) 설치

```bash
uname -m                                   # aarch64 확인
sudo apt update && sudo apt install -y python3-venv
git clone <repo> && cd smart-industrial-safety && git checkout b/ppe-inference
python3 -m venv ~/ppe-venv && . ~/ppe-venv/bin/activate
pip install --upgrade pip
pip install -r edge/inference/requirements.txt   # PyPI 의 aarch64 wheel(onnxruntime, opencv-python-headless) 사용
python -c "import onnxruntime as o; print(o.__version__, o.get_available_providers())"   # CPUExecutionProvider 확인
pip install psutil                                # (선택) RSS 기록 보강
```

### 2) 모델과 측정 프레임 배치

```bash
sudo mkdir -p /models && sudo cp ppe_yolov8n_640.onnx /models/
sha256sum /models/ppe_yolov8n_640.onnx     # 846b747b01505804e384c16b5136b55754a9e64ec2de5f1fe2d32f3ffee64721
# 측정 프레임: 개발 PC 에서 만든 ESP32 모사 프레임을 복사(또는 실제 ESP32 프레임 폴더 사용)
#   PC: python edge/inference/tools/bench_pi.py --export-frames bench_frames/ --video data/test_videos/ppe_safety_test_fixed.mp4
#   scp -r bench_frames/ pi@<pi>:~/smart-industrial-safety/
```

### 3) 스레드 수 비교 (ORT_INTRA_OP_THREADS 1~4)

```bash
mkdir -p results
vcgencmd measure_temp; vcgencmd get_throttled          # 시작 전 온도/스로틀 기록(get_throttled=0x0 이 정상)
for t in 1 2 3 4; do
  ORT_INTRA_OP_THREADS=$t python edge/inference/tools/bench_pi.py \
      --images bench_frames/ --model-path /models/ --iterations 300 --out results/bench_t$t.json
  vcgencmd measure_temp; vcgencmd get_throttled
  sleep 60                                              # 열 회복
done
```

- 결과 JSON: 단계별 `p50/p95/p99/max`(decode/preprocess/inference/postprocess/decision/total), `throughput_fps`,
  `memory`(`ru_maxrss_bytes`, `/proc/self/status` 의 `VmHWM`=RSS 피크, `VmRSS`), 호스트/런타임 정보, `vcgencmd` 값.
- `dev_modules_loaded` 가 `[]` 인지 확인(torch/ultralytics 미적재).
- 측정 중 온도·스로틀 모니터: 다른 터미널에서 `watch -n 2 'vcgencmd measure_temp; vcgencmd get_throttled; vcgencmd measure_clock arm'`.
  `get_throttled` 가 0x0 이 아니면(예: 0x20000=과거 스로틀, 0x4=현재 스로틀) 결과에 함께 기록하고 쿨링을 점검한다.
- 프로세스 최대 RSS 교차 확인: `/usr/bin/time -v python edge/inference/tools/bench_pi.py ... 2>&1 | grep "Maximum resident"`.
- spool 디스크 쓰기를 빼고 순수 처리만 보려면 `--no-publish`.
- 결과는 장치 정보(OS, 커널, 쿨러, 전원, 저장장치)와 함께 `tests/performance/` 규칙(D 소관)에 따라 보고한다.

## Pi 5 권장값

| 항목 | 권장값 | 비고 |
|---|---|---|
| `ORT_INTRA_OP_THREADS` | `2` (팬 없음) / `3` (Active Cooler) | 팬 없이 3 이상이면 스로틀 위험 |
| ESP32 전송 주기 | 500 ms (2 fps) | 펌웨어 설정. Gateway 큐는 장치별 latest-wins 라 더 빨라도 드롭만 늘어남 |

## 남은 이슈

- Pi 5 지연·메모리 미측정(위 절차로 측정 필요). 현재 수치는 x86 참고값뿐.
- 박스 기준·임계값은 테스트 영상 1개 기준 → 실제 ESP32 프레임으로 재조정 필요. 정식 라벨 평가(`tools/eval_status.py`) 미실시.
- ESP32 `edge_detection.persons` 필드는 스키마 1.1.0 대기(현재 스키마로는 edge-frame 검증 실패).
- 모델 라이선스 불일치(MIT vs AGPL-3.0), 데이터셋 출처 불명확 → [`MODEL_CARD.md`](MODEL_CARD.md).
