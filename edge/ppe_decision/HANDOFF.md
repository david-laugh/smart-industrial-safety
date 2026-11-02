# B 파트 인계: PPE 추론 + 판정 파이프라인

대상: C(Gateway / Event Store / Cloud Sync), D(Infra / QA). 작성: B (AI / Vision), 2026-11-02.

> 성능 수치는 모두 **개발 PC(x86) 참고값**이다. Raspberry Pi 5 측정 결과가 아니다.
> 임계값·박스 기준은 **테스트 영상 1개(10초) 기준 잠정값**이며 실제 ESP32 프레임으로 재조정해야 한다.

## 1. 호출 방법 (단일 진입점)

```python
from edge.ppe_decision.pipeline import PPEPipeline
from edge.inference.api import InvalidImageError

pipeline = PPEPipeline.from_env()   # 프로세스당 1개. 모델 세션 + 판정 상태를 수명 동안 유지

def on_frame_selected(edge_frame: dict, jpeg: bytes) -> None:
    """Gateway 프레임 선별기에서 추론할 프레임 1장마다 호출 (워커 1개에서 순차 호출 권장)."""
    try:
        events = pipeline.process(edge_frame, jpeg)   # list[ppe-event], 위반 없으면 []
    except InvalidImageError:
        log.warning("jpeg decode failed, frame dropped: %s", edge_frame["frame_id"])
        return
    except OSError:
        log.exception("spool write failed")  # 해당 이벤트는 dedup 미기록 → 다음 프레임에서 재발행
        return
    # events 는 이미 spool 에 게시됨. 반환값은 로그/메트릭 용도.
    # 추론 timing: pipeline.last_inference_result["timing"]
```

- 시그니처: `PPEPipeline.process(edge_frame: dict, jpeg: bytes) -> list[dict]`
- 블로킹 호출이다(x86 참고 p50 ≈ 65 ms/프레임, intra=3). asyncio 서버라면 단일 스레드 executor 로 넘길 것.
  `process()` 는 내부 잠금으로 직렬화되므로 여러 스레드에서 불러도 안전하지만 병렬 처리되지는 않는다.
- 낮은 수준 API가 필요하면: `edge.inference.api.infer(edge_frame, jpeg) -> inference-result`,
  `edge.ppe_decision.decision.PPEDecision.update(edge_frame, inference_result, jpeg) -> list[ppe-event]`.
- import 경로: 저장소 루트가 `PYTHONPATH` 에 있어야 한다(`edge/` 는 네임스페이스 패키지, `__init__.py` 없음).
  Docker 에서는 예: `WORKDIR /app`, 저장소 복사, `ENV PYTHONPATH=/app`.

## 2. 입력 / 출력

| | 형식 | 비고 |
|---|---|---|
| 입력 `edge_frame` | `contracts/edge-frame.schema.json` | Gateway 가 검증한 dict. `edge_detection` 은 null 가능 |
| 입력 `jpeg` | bytes | `image.width/height` 와 실제 디코드 크기가 다르면 경고 로그만 남기고 실제 크기 사용 |
| 출력 | `list[ppe-event]` (`contracts/ppe-event.schema.json`) | **위반자 1명 = 이벤트 1건**(`violations` 길이 1). 한 프레임에 여러 명이면 여러 건 |

예시(실제 리플레이 출력, 줄바꿈 추가):

```json
{"schema_version":"1.0.0","event_id":"01a19a22-17c4-70a2-b41d-97b23177c9cc","event_type":"PPE_VIOLATION",
 "device_id":"cam-line-a-01","frame_id":"01a199c5-36a4-71c9-b746-4bef9b707209","boot_id":"5f3a9c21","frame_seq":2,
 "timestamps":{"occurred_at_utc":"2026-11-02T01:00:01.000Z","occurred_at_source":"DEVICE_SNTP",
   "capture_ts_utc":"2026-11-02T01:00:01.000Z","mcu_uptime_ms":3601000,
   "gateway_received_at_utc":"2026-11-02T01:00:01.060Z","gateway_clock_synced":true,
   "decided_at_utc":"2026-11-02T02:41:28.004Z"},
 "violations":[{"person_index":1,"track_id":null,
   "bbox":{"x_min":0.356682,"y_min":0.080573,"x_max":0.620772,"y_max":1.0},
   "helmet":{"status":"MISSING","confidence":0.7595},"vest":{"status":"WORN","confidence":0.9251},
   "violation_types":["HELMET_MISSING"]}],
 "image":{"image_path":"images/2026/11/02/01a19a22-17c4-70a2-b41d-97b23177c9cc.jpg","content_type":"image/jpeg",
   "byte_length":34375,"sha256":"06fc869e…ea31","width":640,"height":480},
 "model":{"name":"yolov8n-ppe-hansung-cho","version":"hf-ac0027b-onnx-op12",
   "sha256":"846b747b01505804e384c16b5136b55754a9e64ec2de5f1fe2d32f3ffee64721",
   "input_width":640,"input_height":640,"runtime":"onnxruntime","runtime_version":"1.31.1"},
 "decision":{"policy_version":"0.0.0","dedup_key":"cam-line-a-01|pos:r2c1|HELMET_MISSING|1793581200",
   "dedup_window_s":30,"frames_considered":3}}
```

## 3. spool 경로 규칙 (C: Event Store 가 소비)

`EVENT_DATA_ROOT`(기본 `/data`) 기준:

| 순서 | 파일 | 방법 |
|---|---|---|
| 1 | `images/{yyyy}/{mm}/{dd}/{event_id}.jpg` | `spool/tmp/{event_id}.jpg.part` 에 쓰기 + fsync → rename |
| 2 | `spool/incoming/{event_id}.json` | `spool/tmp/{event_id}.json.part` 에 쓰기 + fsync → rename |

- 날짜 폴더는 `occurred_at_utc` 의 UTC 날짜. `image.image_path` 는 `EVENT_DATA_ROOT` 기준 상대 경로.
- JSON 이 보이면 JPEG 는 이미 완성돼 있다. `.part` 파일은 수집하지 말 것(크래시 잔여물은 정리 대상).
- 같은 `event_id` JSON 이 이미 있으면 덮어쓰지 않고 예외(`FileExistsError`).
- JSON 은 UTF-8, 키 정렬, 공백 없음. `processed/`, `rejected/` 이동은 C 책임(B 는 건드리지 않음).
- 위반자별 이벤트이므로 같은 프레임에서 나온 이벤트들은 **같은 JPEG 내용을 event_id 별 파일로** 각각 갖는다.

## 4. 의존성

- 런타임: `edge/inference/requirements.txt` — numpy, opencv-python-headless, onnxruntime (버전 하한만, 고정은 Pi/Docker Python 버전 확정 후)
- 개발/테스트: `edge/inference/requirements-dev.txt` — ultralytics, jsonschema, referencing, pytest 등
- 런타임 코드는 ultralytics/torch 를 import 하지 않는다. Python 3.11 호환 문법만 사용(개발은 3.12 에서 검증).

## 5. 모델 파일

| 항목 | 값 |
|---|---|
| 배치 | 호스트 `MODEL_DIR`(기본 `./models`) → 컨테이너 `/models` 읽기 전용 마운트 |
| 파일명 | `/models/ppe_yolov8n_640.onnx` (`INFERENCE_MODEL_PATH=/models/` + `INFERENCE_MODEL_FILENAME`) |
| sha256 | `846b747b01505804e384c16b5136b55754a9e64ec2de5f1fe2d32f3ffee64721` |
| 크기 | 12,272,947 bytes, 입력 1x3x640x640 float32, 출력 1x14x8400, opset 12 |
| 원본 | HF `Hansung-Cho/yolov8-ppe-detection` `best.pt` (sha256 `2419700b…0760f`, 커밋 `ac0027b`) |

**모델 파일은 git 에 없다(`.gitignore`). 팀원에게 전달할 방법이 필요하다.** 권장안:

1. B 가 `ppe_yolov8n_640.onnx` 를 팀 공유 드라이브 또는 GitHub Release 첨부파일로 올리고, 받은 쪽이 sha256 을 확인한다.
   ```bash
   sha256sum models/ppe_yolov8n_640.onnx   # 846b747b... 와 일치해야 함
   ```
2. 재생성: `pip install -r edge/inference/requirements-dev.txt` 후
   `python edge/inference/tools/export_onnx.py --imgsz 640` (HF 에서 받은 `best.pt` 필요).
   ultralytics/onnx 버전이 다르면 sha256 이 달라질 수 있으므로 1번을 기준으로 한다.

운영에서는 `INFERENCE_MODEL_SHA256` 을 설정해 두면 로드 시점에 불일치를 거부한다(권장).

## 6. 환경변수

`.env.example` 은 D 소관이므로 수정하지 않았다. 아래 블록을 그대로 복사해 반영하면 된다.

### 6.1 `.env.example` 에 이미 있는 변수 — 권장 값

```dotenv
# ---- Inference (인터페이스 3) ----
INFERENCE_MODEL_PATH=/models/
INFERENCE_MODEL_SHA256=846b747b01505804e384c16b5136b55754a9e64ec2de5f1fe2d32f3ffee64721
ORT_INTRA_OP_THREADS=3
ORT_INTER_OP_THREADS=1
INFERENCE_PERSON_MIN_CONFIDENCE=0.5
# 아래 두 WORN/MISSING 변수가 없을 때 둘 다의 기본값으로만 쓰임
INFERENCE_PPE_MIN_CONFIDENCE=0.5
# 비워 두면 INFERENCE_MIN_PERSON_BOX_RATIO 사용. 설정하면 px 값이 우선
INFERENCE_MIN_PERSON_BOX_PX=

# ---- PPE Decision (인터페이스 4) ----
PPE_POLICY_VERSION=0.0.0
PPE_DECISION_WINDOW_FRAMES=5
PPE_DECISION_MIN_MISSING_FRAMES=3
PPE_UNKNOWN_IS_VIOLATION=false
PPE_DEDUP_WINDOW_S=30
PPE_DEDUP_GRID=4x4
TIME_MAX_DEVICE_SKEW_MS=2000
```

> `PPE_POLICY_VERSION` 은 코드 기본값이 `.env.example` 과 같은 `0.0.0` 이다. 첫 정책 확정 시 올릴 것을 제안(예: `0.1.0`).

### 6.2 B 가 새로 추가한 변수 (모두 선택, 비우면 기본값)

```dotenv
# ---- Inference: 추가 ----
INFERENCE_MODEL_FILENAME=ppe_yolov8n_640.onnx   # INFERENCE_MODEL_PATH 가 디렉터리일 때 파일명
INFERENCE_MODEL_NAME=yolov8n-ppe-hansung-cho    # 이벤트 model.name
INFERENCE_MODEL_VERSION=hf-ac0027b-onnx-op12    # 이벤트 model.version (≤32자)
INFERENCE_PERSON_SOURCE=auto                    # auto | model | esp32 (별칭 PERSON_SOURCE)
INFERENCE_PPE_WORN_MIN_CONFIDENCE=0.5           # 착용 클래스 임계값
INFERENCE_PPE_MISSING_MIN_CONFIDENCE=0.5        # 미착용 클래스 임계값
INFERENCE_MIN_PERSON_BOX_RATIO=0.25             # 판정 가능 최소 사람 박스 높이(이미지 높이 대비)
INFERENCE_DET_MIN_CONFIDENCE=0.25               # NMS 전 후보 하한
INFERENCE_NMS_IOU=0.7                           # 클래스별 NMS IoU
INFERENCE_MAX_DET=300
INFERENCE_HELMET_REGION=0.0,0.25                # 사람 박스 높이 대비 안전모 영역
INFERENCE_VEST_REGION=0.2,0.7                   # 사람 박스 높이 대비 조끼 영역
INFERENCE_ASSOC_MIN_IOA=0.5                     # PPE∩영역 / PPE 면적
INFERENCE_EDGE_MARGIN_PX=2                      # 경계에서 이 거리 이내면 '잘림'
INFERENCE_CLASS_MAP=                            # 예: person=Person,helmet=Hardhat,no_helmet=NO-Hardhat,vest=Safety Vest,no_vest=NO-Safety Vest
INFERENCE_SCHEMA_VERSION=1.0.0                  # inference-result schema_version

# ---- PPE Decision: 추가 ----
PPE_CELL_STICKY_DIST=0.5                        # 격자 단위. 기존 셀 중심과 이 거리 이내면 같은 셀(경계 떨림 방지). 0=비활성
PPE_EVENT_SCHEMA_VERSION=1.0.0                  # ppe-event schema_version
```

개발 전용: `CONTRACTS_DIR`(계약 검증 헬퍼의 스키마 위치, 기본 저장소 `contracts/`).

## 7. 판정 정책 요약 (policy 0.0.0)

- 추론: 단일 모델(Person/Hardhat/NO-Hardhat/Safety Vest/NO-Safety Vest), 클래스별 NMS, 사람 박스 상단 0~25% 안전모 / 20~70% 조끼 영역에 연관.
- 판정 불가(UNKNOWN): 사람 점수 < 0.5, 박스 높이 < 이미지 높이 × 0.25, 부위가 이미지 경계에서 잘림.
- 평활화: (장치, 격자 셀)별 최근 5프레임 중 3프레임 이상 MISSING + 현재 프레임 MISSING → 확정. UNKNOWN 은 세지 않음.
- 판정 불가(helmet·vest 모두 UNKNOWN) 사람은 셀 배정·평활화에서 제외.
- dedup: `{device_id}|pos:r{r}c{c}|{types}|{floor(t/30)*30}`, 같은 (장치, 셀, 유형)은 마지막 발행 후 30초 동안 재발행 안 함.
- `track_id` 는 항상 null(추적기 없음).

## 8. 검증 방법

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r edge/inference/requirements-dev.txt
python -m pytest edge/inference/tests edge/ppe_decision/tests -q
python edge/ppe_decision/tools/run_replay.py --video data/test_videos/ppe_safety_test_fixed.mp4 \
    --model-path edge/inference/models/ --data-root edge/ppe_decision/_data --loops 4
```

리플레이(ESP32 모사 640x480 JPEG q40, 2fps, 40초): 이벤트 4건(위반자 2명 × 30초 창 2개), 모든 출력 스키마 통과.

## 9. 알려진 제한 / 대기 중인 결정

- 추적기 없음 → 사람이 격자 셀을 크게 이동하면 평활화 창이 끊기고 dedup 이 새 셀로 다시 시작될 수 있음.
- 박스 기준(0.25), 임계값(0.5)은 테스트 영상 2개 기준 → 실제 ESP32 프레임으로 재조정 필요.
- `edge_detection.persons`(ESP32 사람 박스)는 스키마 1.1.0 대기. 현재 스키마(`additionalProperties: false`)로는
  edge-frame 검증에 실패하므로, 1.1.0 반영 전까지 `INFERENCE_PERSON_SOURCE=auto`(=모델 Person) 사용.
- Pi 5 지연·메모리 미측정. 측정 도구: `edge/inference/tools/bench_pi.py`(README 참고).
