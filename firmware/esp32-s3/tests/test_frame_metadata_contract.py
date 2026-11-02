"""frame_uploader.ino 와 같은 규칙으로 만든 메타데이터·multipart 가 계약을 지키는지 확인한다.

실행(저장소 루트): python -m pytest firmware/esp32-s3/tests -q
필요: jsonschema, referencing (edge/gateway/requirements.txt). Gateway 왕복 테스트는 fastapi·httpx 가 있으면 실행.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parent
FW = HERE.parent
ROOT = FW.parents[1]
CONTRACTS = ROOT / "contracts"
SKETCH = FW / "frame_uploader" / "frame_uploader.ino"
sys.path.insert(0, str(FW / "tools"))
sys.path.insert(0, str(ROOT))

import frame_sim  # noqa: E402

DEVICE_ID = "cam-line-a-01"
TOKEN = "tok-fw-0123456789"


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schemas = [json.loads(p.read_text(encoding="utf-8")) for p in CONTRACTS.glob("*.schema.json")]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    target = json.loads((CONTRACTS / "esp32-frame-metadata.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(target, registry=registry, format_checker=FormatChecker())


def _meta(ts, edge=None, seq=0):
    return frame_sim.build_metadata(
        device_id=DEVICE_ID, boot_id="9f3c01ab", frame_seq=seq, mcu_uptime_ms=5321,
        capture_ts_utc=ts, width=640, height=480, byte_length=38211, edge=edge)


SYNCED = _meta(frame_sim.format_capture_ts(1793582625120))  # 2026-11-02T01:23:45.120Z
UNSYNCED = _meta(None, seq=1)


def test_format_capture_ts():
    assert frame_sim.format_capture_ts(1793582625120) == "2026-11-02T01:23:45.120Z"
    assert frame_sim.format_capture_ts(1793582625005) == "2026-11-02T01:23:45.005Z"


@pytest.mark.parametrize("meta", [SYNCED, UNSYNCED], ids=["synced", "unsynced"])
def test_metadata_matches_schema(validator, meta):
    errors = [e.message for e in validator.iter_errors(meta)]
    assert errors == []
    assert meta["trigger"] == "PERIODIC"
    assert meta["edge_detection"]["enabled"] is False


def test_unsynced_uses_none_source_and_null_ts():
    assert UNSYNCED["time_sync"] == {"synced": False, "source": "NONE", "capture_ts_utc": None}


def test_edge_detection_enabled_matches_schema(validator):
    meta = _meta(None, edge={"person_detected": True, "person_score": 0.87654, "latency_ms": 41.26})
    assert list(validator.iter_errors(meta)) == []
    assert meta["trigger"] == "EDGE_PERSON_DETECTED"
    assert meta["edge_detection"]["person_score"] == 0.877


def test_schema_rejects_synced_without_ts(validator):
    bad = json.loads(json.dumps(SYNCED))
    bad["time_sync"]["capture_ts_utc"] = None
    assert list(validator.iter_errors(bad)) != []


def test_sketch_constants_match_python_mirror():
    """스케치와 frame_sim.py 의 상수가 어긋나지 않게 한다."""
    src = SKETCH.read_text(encoding="utf-8")

    def const(pattern):
        m = re.search(pattern, src)
        assert m, pattern
        return m.group(1)

    assert const(r'SCHEMA_VERSION\s*=\s*"([^"]+)"') == frame_sim.SCHEMA_VERSION
    assert const(r'FIRMWARE_VERSION\s*=\s*"([^"]+)"') == frame_sim.FIRMWARE_VERSION
    assert int(const(r"JPEG_QUALITY\s*=\s*(\d+)")) == frame_sim.JPEG_QUALITY
    assert const(r'FRAME_SIZE_NAME\s*=\s*"([^"]+)"') == frame_sim.FRAME_SIZE_NAME
    assert const(r'#define EDGE_MODEL_ID "([^"]+)"') == frame_sim.EDGE_MODEL_ID
    assert "FRAMESIZE_VGA" in src and "CAMERA_FB_IN_PSRAM" in src
    # 파트 순서와 헤더
    i_meta = src.index('name=\\"metadata\\"')
    i_img = src.index('name=\\"image\\"; ')
    assert i_meta < i_img
    assert 'filename=\\"frame.jpg\\"' in src


def test_multipart_layout():
    body = frame_sim.build_multipart(b'{"a":1}', b"\xff\xd8JPEG\xff\xd9", "BND")
    assert body.startswith(b"--BND\r\nContent-Disposition: form-data; name=\"metadata\"\r\n")
    assert body.index(b'name="metadata"') < body.index(b'name="image"; filename="frame.jpg"')
    assert body.endswith(b"\xff\xd9\r\n--BND--\r\n")


def test_gateway_accepts_sketch_multipart(tmp_path):
    """스케치와 같은 바이트 배열을 Gateway 앱에 직접 보내 202 를 확인(워커 없음)."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from edge.gateway.app import create_app
    from edge.gateway.config import GatewayConfig

    tokens = tmp_path / "device_tokens"
    tokens.write_text(f"{DEVICE_ID}={TOKEN}\n", encoding="utf-8")
    cfg = GatewayConfig.from_env({"GATEWAY_DEVICE_TOKENS_FILE": str(tokens)})
    jpeg = b"\xff\xd8" + b"\x00" * 1000 + b"\xff\xd9"

    with TestClient(create_app(cfg, None)) as client:
        for meta in (SYNCED, UNSYNCED):
            m = dict(meta, image=dict(meta["image"], byte_length=len(jpeg)))
            boundary = "----esp32frame9f3c01ab00000000"
            body = frame_sim.build_multipart(json.dumps(m, separators=(",", ":")).encode(), jpeg, boundary)
            r = client.post("/api/v1/frames", content=body, headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            })
            assert r.status_code == 202, r.text
            assert r.json()["accepted"] is True
