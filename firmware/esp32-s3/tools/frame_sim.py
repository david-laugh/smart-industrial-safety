"""frame_uploader.ino 의 메타데이터·multipart 규칙을 Python 으로 옮긴 것(테스트·로컬 확인용).

스케치를 바꾸면 이 파일도 같이 바꾼다. 실기 없이 Gateway 에 같은 형식의 요청을 보낼 수 있다:

    python firmware/esp32-s3/tools/frame_sim.py --host 127.0.0.1 --port 8080 \
        --device-id cam-line-a-01 --token <token> --jpeg frame.jpg [--count 5]
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone
from typing import Optional

SCHEMA_VERSION = "1.0.0"
FIRMWARE_VERSION = "0.1.0"
JPEG_QUALITY = 12
FRAME_SIZE_NAME = "VGA"
EDGE_MODEL_ID = "stub-none"


def format_capture_ts(epoch_ms: int) -> str:
    """스케치 captureTsUtc() 와 같은 형식: YYYY-MM-DDTHH:MM:SS.mmmZ"""
    dt = datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{epoch_ms % 1000:03d}Z"


def build_metadata(
    *,
    device_id: str,
    boot_id: str,
    frame_seq: int,
    mcu_uptime_ms: int,
    capture_ts_utc: Optional[str],
    width: int,
    height: int,
    byte_length: int,
    edge: Optional[dict] = None,
) -> dict:
    """스케치 buildMetadata() 와 같은 규칙.

    edge=None → 감지 비활성(PERIODIC). edge={"person_detected", "person_score", "latency_ms"} → EDGE_PERSON_DETECTED.
    """
    if capture_ts_utc is not None:
        time_sync = {"synced": True, "source": "SNTP", "capture_ts_utc": capture_ts_utc}
    else:
        time_sync = {"synced": False, "source": "NONE", "capture_ts_utc": None}
    if edge is None:
        trigger = "PERIODIC"
        edge_detection = {"enabled": False, "person_detected": None, "person_score": None,
                          "model_id": None, "latency_ms": None}
    else:
        trigger = "EDGE_PERSON_DETECTED"
        edge_detection = {"enabled": True, "person_detected": bool(edge["person_detected"]),
                          "person_score": round(min(1.0, max(0.0, edge["person_score"])), 3),
                          "model_id": EDGE_MODEL_ID, "latency_ms": round(edge["latency_ms"], 1)}
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id,
        "boot_id": boot_id,
        "frame_seq": frame_seq,
        "mcu_uptime_ms": mcu_uptime_ms,
        "time_sync": time_sync,
        "trigger": trigger,
        "image": {"content_type": "image/jpeg", "width": width, "height": height,
                  "byte_length": byte_length, "jpeg_quality": JPEG_QUALITY,
                  "frame_size": FRAME_SIZE_NAME},
        "edge_detection": edge_detection,
        "firmware_version": FIRMWARE_VERSION,
    }


def build_multipart(meta_json: bytes, jpeg: bytes, boundary: str) -> bytes:
    """스케치 postFrame() 과 같은 바이트 배열: metadata → image → 종료 경계."""
    return b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\n"
        "Content-Type: application/json\r\n\r\n".encode(),
        meta_json,
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
        "filename=\"frame.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode(),
        jpeg,
        f"\r\n--{boundary}--\r\n".encode(),
    ])


def jpeg_size(jpeg: bytes) -> tuple[int, int]:
    """SOFn 마커에서 (width, height)."""
    i = 2
    while i + 9 < len(jpeg):
        if jpeg[i] != 0xFF:
            i += 1
            continue
        marker = jpeg[i + 1]
        seg_len = struct.unpack(">H", jpeg[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", jpeg[i + 5:i + 9])
            return w, h
        i += 2 + seg_len
    raise ValueError("JPEG SOF marker not found")


def post_frame(host: str, port: int, token: str, meta: dict, jpeg: bytes, boot_id: str) -> tuple[int, dict]:
    boundary = f"----esp32frame{boot_id}{os.urandom(4).hex()}"
    meta_json = json.dumps(meta, separators=(",", ":")).encode()
    body = build_multipart(meta_json, jpeg, boundary)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("POST", "/api/v1/frames", body=body, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Connection": "close",
        })
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--jpeg", required=True)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--unsynced", action="store_true", help="time_sync.synced=false 로 보냄")
    a = ap.parse_args(argv)

    jpeg = open(a.jpeg, "rb").read()
    w, h = jpeg_size(jpeg)
    boot_id = os.urandom(4).hex()
    t_boot = time.monotonic()
    worst = 0
    for seq in range(a.count):
        uptime = int((time.monotonic() - t_boot) * 1000)
        ts = None if a.unsynced else format_capture_ts(int(time.time() * 1000))
        meta = build_metadata(device_id=a.device_id, boot_id=boot_id, frame_seq=seq, mcu_uptime_ms=uptime,
                              capture_ts_utc=ts, width=w, height=h, byte_length=len(jpeg))
        t0 = time.monotonic()
        try:
            status, body = post_frame(a.host, a.port, a.token, meta, jpeg, boot_id)
        except OSError as e:  # 스케치의 네트워크 오류(http=-1)
            status, body = -1, {"error": str(e)}
        ms = int((time.monotonic() - t0) * 1000)
        print(f"[frame] seq={seq} jpeg={len(jpeg)}B http={status} ms={ms} {json.dumps(body)}")
        worst = max(worst, 0 if status == 202 else 1)
        if seq + 1 < a.count:
            time.sleep(0.5)
    return worst


if __name__ == "__main__":
    sys.exit(main())
