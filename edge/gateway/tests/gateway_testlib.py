"""gateway 테스트 공용 헬퍼 (conftest 와 이름이 겹치지 않도록 별도 모듈)."""
import copy
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "contracts" / "examples"
TOKENS = {
    "cam-line-a-01": "tok-a-0123456789",
    "cam-line-b-01": "tok-b-0123456789",
    "cam-line-c-01": "tok-c-0123456789",
}


def load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def fake_jpeg(n: int) -> bytes:
    """SOI + 채움 + EOI, 총 n 바이트 (디코드 가능한 JPEG 는 아님 — Gateway 는 마커만 검사)."""
    assert n >= 4
    return b"\xff\xd8" + b"\x00" * (n - 4) + b"\xff\xd9"


def make_metadata(device_id="cam-line-a-01", n=40960, person=None, seq=None,
                  example="esp32-frame-metadata.example.json"):
    m = copy.deepcopy(load_example(example))
    m["device_id"] = device_id
    m["image"]["byte_length"] = n
    if seq is not None:
        m["frame_seq"] = seq
    if person is not None:
        m["edge_detection"] = {
            "enabled": True,
            "person_detected": person,
            "person_score": 0.9 if person else 0.1,
            "model_id": "test",
            "latency_ms": None,
        }
    return m


def auth(device_id="cam-line-a-01"):
    return {"Authorization": f"Bearer {TOKENS[device_id]}"}


def files(metadata, jpeg, image_type="image/jpeg"):
    meta = metadata if isinstance(metadata, (bytes, str)) else json.dumps(metadata)
    return [
        ("metadata", (None, meta, "application/json")),
        ("image", ("frame.jpg", jpeg, image_type)),
    ]


class FakePipeline:
    """PPEPipeline.process 대역. raise_on 에 든 frame_seq 에서 예외를 던진다."""

    def __init__(self, raise_on=(), expected=None):
        self.calls = []
        self.raise_on = set(raise_on)
        self.expected = expected
        self.done = threading.Event()

    def process(self, edge_frame, jpeg):
        self.calls.append((edge_frame, jpeg))
        try:
            if edge_frame["frame_seq"] in self.raise_on:
                raise RuntimeError("boom")
            return []
        finally:
            if self.expected is not None and len(self.calls) >= self.expected:
                self.done.set()


class FixedClock:
    def __init__(self, value=True):
        self.value = value

    def synced(self):
        return self.value


