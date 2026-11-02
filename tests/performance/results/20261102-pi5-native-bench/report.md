# 측정 보고서: Pi 5 native 추론 파이프라인 벤치마크 (2026-11-02)

- 담당: D (B 요청 실측) · 대상: `PPEPipeline` (edge.inference + edge.ppe_decision) 단독
- 기간: 2026-11-02T04:12:10Z ~ 04:24:24Z
- **범위 밖**: Gateway, cloud-sync, Docker. 이 결과로 시스템 전체 3.5 GB 제약을 판정하지 않는다.
- 원시 데이터: `raw/` (bench JSON 7개, 로그, 호스트 메모리 CSV 712샘플, 실행 스크립트) — 커밋하지 않음

## 환경

| 항목 | 값 |
|---|---|
| 보드 / OS | Raspberry Pi 5 Model B Rev 1.1, 4GB / Debian 13 trixie, 커널 6.18.50+rpt-rpi-2712, aarch64 |
| 배포 방식 | native, 사용자 venv `~/ppe-bench/.venv` (Docker 아님, sudo 없음) |
| 런타임 | Python 3.13.5, onnxruntime 1.31.1 (CPUExecutionProvider), opencv-python-headless 5.0.0, numpy 2.5.4, psutil 7.2.2 |
| 코드 | `master` 52b950d (`git archive`로 복사) |
| 모델 | `ppe_yolov8n_640.onnx`, sha256 `846b747b01505804e384c16b5136b55754a9e64ec2de5f1fe2d32f3ffee64721` (일치 확인), 입력 640×640 |
| 냉각 | 팬 없음 · 저장장치 microSD · 전원 공식 여부 미확인 |

## 입력

| 항목 | 값 |
|---|---|
| 원본 | 테스트 영상 3개 (1280×720, 24 fps, 각 10초) → Pi에서 `video_1~3.mp4`로 이름 변경 |
| 프레임 | `bench_pi.py --export-frames` 기본값: 2 fps, 640×480, JPEG q40, 영상당 20장 → `all` 60장 |
| sha256 | video_1 `fb45f8d7…1ab2`, video_2 `8753b858…e802`, video_3 `b965e811…3ed6` |
| 주의 | 16:9 원본을 4:3으로 비율 무시 리사이즈 → 지연 측정에는 영향 미미, 검출 품질 평가에는 부적합 |

## 실행 명령 (Pi, `~/ppe-bench`)

```bash
python -m pytest edge -q
for t in 1 2 3 4; do
  ORT_INTRA_OP_THREADS=$t python edge/inference/tools/bench_pi.py --images bench_frames/all \
     --model-path edge/inference/models/ --iterations 300 --out $RUN/bench_all_t$t.json
  sleep 60
done
for i in 1 2 3; do
  ORT_INTRA_OP_THREADS=3 python edge/inference/tools/bench_pi.py --images bench_frames/video_$i \
     --model-path edge/inference/models/ --iterations 120 --out $RUN/bench_video${i}_t3.json
  sleep 30
done
```

warmup 10, spool 게시 포함(임시 디렉터리). 병행해서 `/proc/meminfo`, 온도, `vcgencmd get_throttled`, ARM 클럭을 1초 간격으로 기록.

## 결과 1: pytest

| 조건 | 결과 |
|---|---|
| `python -m pytest edge -q` (dev 프레임 없음) | **52 passed, 2 skipped** (1.20 s) |
| 새 영상 프레임을 `edge/inference/_dev/frames/esp32`에 배치 | 52 passed, **2 failed** |

실패 2건(`test_infer_on_real_frame_if_available`, `test_pipeline_process_publishes_one_event_per_violator`)은 B의 기존 테스트 영상 기준 기대값이 하드코딩된 테스트다. 환경 결함이 아니라 테스트 데이터 의존성이다.

## 결과 2: 지연 (ms)

| 실행 | 스레드 | 반복 | fps | 추론 p50/p95/p99 | 전체 p50/p95/p99/max | 이벤트 | 측정 중 스로틀 |
|---|---|---|---|---|---|---|---|
| all | 1 | 300 | 2.71 | 359.1 / 361.1 / 365.7 | 368.3 / 370.2 / 375.3 / 377.1 | 25 | 없음 (최고 61.7 °C) |
| all | 2 | 300 | 4.94 | 192.7 / 195.8 / 202.1 | 201.9 / 205.0 / 212.4 / 218.2 | 25 | 없음 (최고 74.4 °C) |
| all | 3 | 300 | 6.13 | 151.2 / 162.6 / 172.3 | 160.5 / 172.1 / 181.7 / 197.0 | 25 | **발생** (최고 83.7 °C) |
| all | 4 | 300 | 6.13 | 148.6 / 183.1 / 199.2 | 159.0 / 193.1 / 209.5 / 220.5 | 25 | **발생** (최고 85.9 °C, 최저 1.5 GHz) |
| video_1 | 3 | 120 | 6.20 | 150.9 / 154.9 / 156.0 | 160.3 / 164.7 / 166.2 / 176.7 | 4 | 발생 이력(0xe0000) |
| video_2 | 3 | 120 | 6.23 | 149.8 / 155.9 / 159.6 | 158.8 / 165.7 / 169.1 / 179.4 | 4 | 발생 |
| video_3 | 3 | 120 | 6.19 | 150.4 / 157.8 / 163.1 | 159.4 / 167.1 / 172.2 / 176.1 | 2 | 발생 |

추론 외 단계(전 실행 공통 범위, p50): decode 1.63–1.77, 전처리 5.29–5.77, 후처리 1.32–1.81, decision 0.39–0.42. 모델 로드 42.0–43.0 ms. `dev_modules_loaded=[]`.

## 결과 3: 메모리

| 지표 | 값 |
|---|---|
| 프로세스 RSS 시작 → 로드 후 → 측정 후 (all, t3) | 52,494,336 → 104,939,520 → 158,826,496 B |
| 프로세스 VmHWM (모든 실행) | 167,886,848 ~ 170,147,840 B |
| 호스트 사용량 Idle (측정 전 30초) | 평균 261,630,634 B, 최대 266,731,520 B |
| 호스트 사용량 부하 중 최대 | **394,297,344 B (376.0 MiB)** |
| 호스트 사용량 Idle (측정 후 30초) | 평균 263,885,788 B |
| Swap 사용 | 0 B (전 구간) |

## 결과 4: 열 / 스로틀

| 구간 | 최고 온도 | `get_throttled` | 최저 ARM 클럭 |
|---|---|---|---|
| idle | 45.2 °C | 0x0 | — |
| t1 | 61.7 °C | 0x0 | 2.40 GHz |
| t2 | 74.4 °C | 0x0 | 2.40 GHz |
| t3 | 83.7 °C | 0x0 → 0x80000, 0x80008, 0xe0008 | 2.15 GHz |
| t4 | 85.9 °C | 0xe0000, 0xe0006, 0xe0008 | 1.50 GHz |

비트 해석: bit3 soft temp limit 활성, bit1/bit2 클럭 제한·스로틀 활성, bit17–19 발생 이력. 저전압 비트(bit0, bit16)는 없음 → 원인은 전원이 아니라 **온도**.

## 판정

| 항목 | 판정 |
|---|---|
| Pi 5 ARM64에서 B 파이프라인 동작 | 확인 (스키마 검증 테스트 포함 52 passed) |
| 스레드 수에 따른 처리량 | t1 → t2 → t3 개선. **t3/t4는 스로틀 상태라 비교 무효** — 냉각 후 재측정 필요 |
| 파이프라인 단독 메모리 | 프로세스 최대 약 170 MB, 호스트 최대 394,297,344 B |
| SYS-MEM-01 (시스템 전체 < 3,500,000,000 B) | **미판정** — Gateway/cloud-sync/Docker 미포함 |
| 장시간 메모리 안정성 | **미검증** (12분 단기 측정만) |

## 후속 조치

1. Active Cooler 장착 후 t3/t4 재측정 (같은 프레임, 같은 명령)
2. 메모리 cgroup 활성화 → Docker 설치 → Docker Lite와 native 비교
3. 60분 이상 장시간 테스트 (메모리 증가 기울기, 처리량 저하 확인)
4. B: 테스트 픽스처 프레임 의존성 정리, Python 3.13 / opencv 5.0 / numpy 2.5 조합 확인
