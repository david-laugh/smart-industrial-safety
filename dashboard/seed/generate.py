"""계약(contracts/)을 따르는 가데이터 이벤트와 증거 JPEG를 만든다.

- 행: supabase-event-record (ingested_at 제외 — 서버 생성 컬럼)
- event_payload: ppe-event
- 규칙: docs/protocols.md §시간 규칙(occurred_at 결정), §중복 방지(dedup_key), §ID 규칙(UUIDv7),
        cloud/README.md §2(storage_path)
- 위반자 1명 = 이벤트 1건(dedup_key 가 사람 1명 기준, B 파트 결정). 한 프레임에 위반자가 여럿이면
  같은 frame_id·같은 JPEG 로 이벤트가 여러 건 생기고, Storage 에는 event_id 경로마다 같은 JPEG 가 저장된다.
"""

import hashlib
import io
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image, ImageDraw
from referencing import Registry, Resource

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"

DEVICES = ["cam-line-a-01", "cam-line-a-02", "cam-line-b-01", "cam-warehouse-01"]
BUCKET = "ppe-evidence"
IMAGE_W, IMAGE_H = 640, 480
TIME_MAX_DEVICE_SKEW_MS = 2000  # infra/docker/.env.example
DEDUP_WINDOW_S = 30  # PPE_DEDUP_WINDOW_S
DEDUP_GRID = 4  # PPE_DEDUP_GRID=4x4
MODEL = {
    "name": "ppe-detector",
    "version": "0.1.0",
    "input_width": 640,
    "input_height": 640,
    "runtime": "onnxruntime",
    "runtime_version": "1.19.2",
}


def utc_iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def uuid7(rng, dt):
    """RFC 9562 UUIDv7: 48비트 Unix ms + 버전 7 + 변형 10."""
    ms = int(dt.timestamp() * 1000)
    rand_a = rng.getrandbits(12)
    rand_b = rng.getrandbits(62)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def dedup_key(device_id, v, occurred):
    cell = lambda x: min(DEDUP_GRID - 1, int(x * DEDUP_GRID))  # noqa: E731
    cx = (v["bbox"]["x_min"] + v["bbox"]["x_max"]) / 2
    cy = (v["bbox"]["y_min"] + v["bbox"]["y_max"]) / 2
    who = v["track_id"] or f"pos:r{cell(cy)}c{cell(cx)}"
    types = ",".join(sorted(v["violation_types"]))
    window_start = int(occurred.timestamp()) // DEDUP_WINDOW_S * DEDUP_WINDOW_S
    return f"{device_id}|{who}|{types}|{window_start}"


def _item(rng, missing):
    if missing:
        return {"status": "MISSING", "confidence": round(rng.uniform(0.55, 0.95), 2)}
    if rng.random() < 0.15:  # UNKNOWN 은 위반이 아님(PPE_UNKNOWN_IS_VIOLATION=false)
        return {"status": "UNKNOWN", "confidence": None if rng.random() < 0.5 else round(rng.uniform(0.2, 0.5), 2)}
    return {"status": "WORN", "confidence": round(rng.uniform(0.6, 0.98), 2)}


def _people(rng):
    """프레임 속 사람들. 위반자(violation_types 비어 있지 않음)가 최소 1명, 나머지는 준수자일 수 있다."""
    n = 1 if rng.random() < 0.6 else (2 if rng.random() < 0.7 else 3)
    slot = 1 / n
    violator = rng.randrange(n)  # 최소 1명 보장
    out = []
    for i in range(n):
        if i != violator and rng.random() < 0.4:
            helmet_missing = vest_missing = False  # 준수자(이벤트 없음, 이미지에만 등장)
        else:
            r = rng.random()
            helmet_missing = r < 0.55 or r >= 0.85
            vest_missing = r >= 0.55
        w = min(slot * 0.75, rng.uniform(0.16, 0.28))
        h = rng.uniform(0.55, 0.9)
        x0 = i * slot + rng.random() * (slot - w)
        y0 = max(0.02, 0.98 - h - rng.random() * 0.06)
        types = (["HELMET_MISSING"] if helmet_missing else []) + (["VEST_MISSING"] if vest_missing else [])
        out.append(
            {
                "person_index": i,
                "track_id": None if rng.random() < 0.5 else f"trk-{rng.getrandbits(24):06x}",
                "bbox": {
                    "x_min": round(x0, 2),
                    "y_min": round(y0, 2),
                    "x_max": round(min(1, x0 + w), 2),
                    "y_max": round(min(1, y0 + h), 2),
                },
                "helmet": _item(rng, helmet_missing),
                "vest": _item(rng, vest_missing),
                "violation_types": types,
            }
        )
    return out


def render_jpeg(device_id, occurred_iso, people):
    """bbox 위치에 작업자를 그린 640x480 증거 JPEG."""
    img = Image.new("RGB", (IMAGE_W, IMAGE_H), (138, 144, 151))
    d = ImageDraw.Draw(img)
    floor_y = int(IMAGE_H * 0.62)
    d.rectangle([0, floor_y, IMAGE_W, IMAGE_H], fill=(111, 106, 96))
    d.rectangle([40, int(IMAGE_H * 0.18), 190, floor_y], fill=(116, 122, 128))
    d.rectangle([430, int(IMAGE_H * 0.26), 600, floor_y], fill=(123, 129, 135))
    for x in range(0, IMAGE_W, 46):
        d.rectangle([x, int(IMAGE_H * 0.855), x + 28, int(IMAGE_H * 0.865)], fill=(217, 180, 0))
    for v in people:
        b = v["bbox"]
        x, y = b["x_min"] * IMAGE_W, b["y_min"] * IMAGE_H
        w, h = (b["x_max"] - b["x_min"]) * IMAGE_W, (b["y_max"] - b["y_min"]) * IMAGE_H
        cx = x + w / 2
        r = min(w * 0.22, h * 0.09)
        head_cy = y + r * 1.6
        body_top = head_cy + r * 1.1
        body_h, body_w = h * 0.42, w * 0.62
        leg_top = body_top + body_h
        d.rectangle([cx - body_w * 0.42, leg_top, cx - body_w * 0.06, y + h], fill=(47, 54, 64))
        d.rectangle([cx + body_w * 0.06, leg_top, cx + body_w * 0.42, y + h], fill=(47, 54, 64))
        torso = (242, 113, 28) if v["vest"]["status"] == "WORN" else (75, 90, 107)
        d.rounded_rectangle([cx - body_w / 2, body_top, cx + body_w / 2, leg_top], radius=body_w * 0.18, fill=torso)
        if v["vest"]["status"] == "WORN":
            sy = body_top + body_h * 0.45
            d.rectangle([cx - body_w / 2, sy, cx + body_w / 2, sy + max(3, body_h * 0.07)], fill=(232, 232, 224))
        d.ellipse([cx - r, head_cy - r, cx + r, head_cy + r], fill=(201, 155, 123))
        if v["helmet"]["status"] == "WORN":
            hr = r * 1.25
            d.pieslice([cx - hr, head_cy - hr - r * 0.1, cx + hr, head_cy + hr - r * 0.1], 180, 360, fill=(247, 200, 0))
        else:
            d.pieslice([cx - r, head_cy - r - r * 0.2, cx + r, head_cy + r - r * 0.2], 180, 360, fill=(58, 42, 32))
    d.text((12, 10), f"{device_id}  {occurred_iso}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def make_frame_events(rng, occurred_target, device_id):
    """프레임 1장 → 위반자마다 이벤트 1건 [(row, jpeg_bytes), ...]. occurred_target 은 대략적 촬영 시각.

    같은 프레임의 이벤트는 frame_id·boot_id·frame_seq·시각·JPEG(sha256)를 공유하고 event_id·violations·dedup_key 만 다르다.
    """
    gateway = occurred_target + timedelta(milliseconds=rng.uniform(40, 200))
    decided = gateway + timedelta(milliseconds=rng.uniform(300, 1500))
    r = rng.random()
    if r < 0.8:  # SNTP 동기화, 오차 정상
        capture = occurred_target
    elif r < 0.9:  # SNTP 동기화됐지만 시계 오차가 임계값 초과
        capture = gateway - timedelta(milliseconds=rng.uniform(TIME_MAX_DEVICE_SKEW_MS + 500, 60000))
    else:  # SNTP 미동기화
        capture = None
    # docs/protocols.md §시간 규칙: occurred_at 결정
    if capture is not None and abs((capture - gateway).total_seconds() * 1000) <= TIME_MAX_DEVICE_SKEW_MS:
        occurred, source = capture, "DEVICE_SNTP"
    else:
        occurred, source = gateway, "GATEWAY_RECEIVED"

    people = _people(rng)
    occurred_iso = utc_iso(occurred)
    jpeg = render_jpeg(device_id, occurred_iso, people)
    sha = hashlib.sha256(jpeg).hexdigest()
    day = occurred.astimezone(timezone.utc).strftime("%Y/%m/%d")
    frame = {
        "frame_id": uuid7(rng, gateway),
        "boot_id": f"{rng.getrandbits(32):08x}",
        "frame_seq": rng.randrange(200000),
        "mcu_uptime_ms": rng.randrange(86_400_000),
        "frames_considered": rng.randint(3, 5),
    }
    model = {**MODEL, "sha256": hashlib.sha256(f"{MODEL['name']}:{MODEL['version']}".encode()).hexdigest()}

    events = []
    for person in (p for p in people if p["violation_types"]):
        event_id = uuid7(rng, decided)
        payload = {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "event_type": "PPE_VIOLATION",
            "device_id": device_id,
            "frame_id": frame["frame_id"],
            "boot_id": frame["boot_id"],
            "frame_seq": frame["frame_seq"],
            "timestamps": {
                "occurred_at_utc": occurred_iso,
                "occurred_at_source": source,
                "capture_ts_utc": utc_iso(capture) if capture else None,
                "mcu_uptime_ms": frame["mcu_uptime_ms"],
                "gateway_received_at_utc": utc_iso(gateway),
                "gateway_clock_synced": True,
                "decided_at_utc": utc_iso(decided),
            },
            "violations": [person],
            "image": {
                "image_path": f"images/{day}/{event_id}.jpg",
                "content_type": "image/jpeg",
                "byte_length": len(jpeg),
                "sha256": sha,
                "width": IMAGE_W,
                "height": IMAGE_H,
            },
            "model": model,
            "decision": {
                "policy_version": "0.1.0",
                "dedup_key": dedup_key(device_id, person, occurred),
                "dedup_window_s": DEDUP_WINDOW_S,
                "frames_considered": frame["frames_considered"],
            },
        }
        row = {
            "event_id": event_id,
            "schema_version": "1.0.0",
            "device_id": device_id,
            "occurred_at": occurred_iso,
            "occurred_at_source": source,
            "gateway_received_at": utc_iso(gateway),
            "decided_at": utc_iso(decided),
            "violation_count": 1,
            "violation_types": sorted(person["violation_types"]),
            "storage_bucket": BUCKET,
            "storage_path": f"{device_id}/{day}/{event_id}.jpg",
            "image_sha256": sha,
            "model_name": MODEL["name"],
            "model_version": MODEL["version"],
            "event_payload": payload,
        }
        events.append((row, jpeg))
    return events


def generate(count, days, seed, now=None):
    """근무 시간대(UTC+9 07~19시)에 몰린 이벤트 count 건, occurred_at 오래된 순(업로드 순서).

    프레임 단위로 만들기 때문에 마지막 프레임의 이벤트 일부가 잘릴 수 있다(count 를 정확히 맞춤).
    """
    rng = random.Random(seed)
    now = now or datetime.now(timezone.utc)
    events = []
    while len(events) < count:
        t = now - timedelta(seconds=rng.uniform(60, days * 86400))
        local_hour = (t.hour + 9) % 24
        if not (7 <= local_hour < 19) and rng.random() > 0.12:
            continue
        events.extend(make_frame_events(rng, t, rng.choice(DEVICES)))
    events = events[:count]
    events.sort(key=lambda e: (e[0]["occurred_at"], e[0]["event_id"]))
    return events


def validator():
    """contracts/supabase-event-record 검증기($ref 는 contracts/ 레지스트리로 해석)."""
    schemas = [json.loads(p.read_text(encoding="utf-8")) for p in CONTRACTS_DIR.glob("*.schema.json")]
    registry = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in schemas])
    main = next(s for s in schemas if s["$id"].endswith("/supabase-event-record.schema.json"))
    return Draft202012Validator(main, registry=registry, format_checker=FormatChecker())
