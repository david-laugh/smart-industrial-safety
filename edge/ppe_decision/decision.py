"""PPEDecision: 프레임 단위 inference-result → 다중 프레임 평활화 → 위반 확정 → dedup → ppe-event.

정책(초기안, DecisionConfig 로 조정, 바꾸면 PPE_POLICY_VERSION 증가):
- 같은 사람 식별(추적기 없음): bbox 중심을 PPE_DEDUP_GRID 격자로 양자화한 셀 "pos:r{행}c{열}".
  격자 경계 떨림 방지(sticky): 해당 장치에서 이미 추적 중인 셀의 마지막 중심과 거리(격자 단위)가
  PPE_CELL_STICKY_DIST(기본 0.5) 이하이면 가장 가까운 기존 셀을 그대로 쓴다.
- 평활화: (device_id, 셀)마다 해당 장치의 최근 PPE_DECISION_WINDOW_FRAMES 프레임 상태를 보관.
  그 프레임에 셀에 사람이 없으면 ABSENT 로 기록(미착용으로 세지 않음).
  항목(helmet/vest)별로 창 안 MISSING 수 ≥ PPE_DECISION_MIN_MISSING_FRAMES 이고
  **현재 프레임도 MISSING** 이면 위반 확정(증거 이미지에 위반이 보이도록).
  UNKNOWN 은 PPE_UNKNOWN_IS_VIOLATION=true 일 때만 MISSING 으로 센다.
  같은 셀에 여러 사람이 있으면 항목별 우선순위 MISSING > WORN > UNKNOWN 으로 셀 상태를 정한다.
- 위반 유형: HELMET_MISSING, VEST_MISSING (정렬).
- dedup_key = "{device_id}|pos:r{r}c{c}|{정렬 위반유형 ,연결}|{window_start_epoch_s}"
  window_start_epoch_s = floor(occurred_epoch_s / PPE_DEDUP_WINDOW_S) * PPE_DEDUP_WINDOW_S
  억제: 같은 (device_id, 셀, 위반유형) 이 마지막 발행 후 PPE_DEDUP_WINDOW_S 초 안이면 재발행하지 않음
  (버킷 경계를 넘어도 창 안이면 억제). 같은 키도 재발행하지 않음.
- 같은 프레임의 여러 위반자는 이벤트 1건(violations 배열)으로 묶는다.
  decision.dedup_key 는 스키마상 문자열 1개이므로 person_index 가 가장 작은 위반자의 키를 쓴다.
- 결정적 동작: 같은 입력 시퀀스 → 같은 판정. event_id, decided_at 만 다르며 둘 다 주입 가능.
- 시간: capture_ts_utc 가 있고 |capture − gateway_received| ≤ TIME_MAX_DEVICE_SKEW_MS → DEVICE_SNTP,
  아니면 GATEWAY_RECEIVED. 모든 시각은 'Z' 접미사 UTC.
"""
from __future__ import annotations

import hashlib
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from .config import DecisionConfig
from .spool import SpoolPublisher
from .uuid7 import uuid7

log = logging.getLogger(__name__)

WORN, MISSING, UNKNOWN, ABSENT = "WORN", "MISSING", "UNKNOWN", "ABSENT"
ITEMS = (("helmet", "HELMET_MISSING"), ("vest", "VEST_MISSING"))
_PRIORITY = {MISSING: 3, WORN: 2, UNKNOWN: 1, ABSENT: 0}

Cell = Tuple[int, int]


def parse_utc(s: str) -> datetime:
    if not s.endswith("Z"):
        raise ValueError(f"UTC timestamp must end with 'Z': {s}")
    return datetime.fromisoformat(s[:-1] + "+00:00")


def format_utc(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def occurred_at(edge_frame: Dict[str, Any], max_skew_ms: int) -> Tuple[str, str]:
    gw = edge_frame["gateway_received_at_utc"]
    cap = edge_frame.get("capture_ts_utc")
    if cap:
        try:
            skew = abs((parse_utc(cap) - parse_utc(gw)).total_seconds() * 1000.0)
            if skew <= max_skew_ms:
                return cap, "DEVICE_SNTP"
        except ValueError:
            log.warning("invalid capture_ts_utc %r (frame_id=%s)", cap, edge_frame.get("frame_id"))
    return gw, "GATEWAY_RECEIVED"


def jpeg_size(jpeg: bytes) -> Optional[Tuple[int, int]]:
    """JPEG SOF 마커에서 (width, height) 읽기(디코드 없이, 표준 라이브러리만)."""
    i, n = 2, len(jpeg)
    if jpeg[:2] != b"\xff\xd8":
        return None
    while i + 4 <= n:
        if jpeg[i] != 0xFF:
            i += 1
            continue
        marker = jpeg[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        seg_len = int.from_bytes(jpeg[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 > n:
                return None
            h = int.from_bytes(jpeg[i + 5:i + 7], "big")
            w = int.from_bytes(jpeg[i + 7:i + 9], "big")
            return w, h
        i += 2 + seg_len
    return None


class PPEDecision:
    def __init__(
        self,
        cfg: Optional[DecisionConfig] = None,
        publisher: Optional[SpoolPublisher] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        id_fn: Optional[Callable[[], str]] = None,
        publish: bool = True,
    ) -> None:
        self.cfg = cfg or DecisionConfig.from_env()
        self.publisher = publisher if publisher is not None else (SpoolPublisher(self.cfg.data_root) if publish else None)
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.id_fn = id_fn or uuid7
        # (device_id, cell) → deque[(helmet_status, vest_status)]
        self._windows: Dict[Tuple[str, Cell], Deque[Tuple[str, str]]] = {}
        # (device_id, cell) → 마지막 중심(격자 단위 x, y)
        self._centers: Dict[Tuple[str, Cell], Tuple[float, float]] = {}
        # (device_id, cell, types) → (last_emit_epoch_s, last_key)
        self._emitted: Dict[Tuple[str, Cell, str], Tuple[float, str]] = {}

    # ------------------------------------------------------------------
    def _grid_center(self, bbox: Dict[str, float]) -> Tuple[float, float]:
        cx = (bbox["x_min"] + bbox["x_max"]) / 2 * self.cfg.grid_cols
        cy = (bbox["y_min"] + bbox["y_max"]) / 2 * self.cfg.grid_rows
        return cx, cy

    def cell_of(self, bbox: Dict[str, float]) -> Cell:
        gx, gy = self._grid_center(bbox)
        r = min(self.cfg.grid_rows - 1, max(0, int(gy)))
        c = min(self.cfg.grid_cols - 1, max(0, int(gx)))
        return r, c

    def assign_cell(self, device_id: str, bbox: Dict[str, float]) -> Cell:
        gx, gy = self._grid_center(bbox)
        best, best_d = None, None
        for (dev, cell), (x, y) in self._centers.items():
            if dev != device_id:
                continue
            d = ((gx - x) ** 2 + (gy - y) ** 2) ** 0.5
            if d <= self.cfg.cell_sticky_dist and (best_d is None or (d, cell) < (best_d, best)):
                best, best_d = cell, d
        return best if best is not None else self.cell_of(bbox)

    def _counts_as_missing(self, status: str) -> bool:
        return status == MISSING or (self.cfg.unknown_is_violation and status == UNKNOWN)

    def _update_windows(self, device_id: str, persons: List[Dict[str, Any]]) -> Dict[int, Cell]:
        """사람별 셀 배정 후 창 갱신. 반환: {person_index: cell}"""
        by_cell: Dict[Cell, List[Dict[str, Any]]] = {}
        assigned: Dict[int, Cell] = {}
        for p in sorted(persons, key=lambda q: q["person_index"]):
            cell = self.assign_cell(device_id, p["bbox"])
            assigned[p["person_index"]] = cell
            by_cell.setdefault(cell, []).append(p)
        cells_now = {}
        for cell, ps in by_cell.items():
            st = []
            for item, _ in ITEMS:
                st.append(max((p[item]["status"] for p in ps), key=lambda s: _PRIORITY[s]))
            cells_now[cell] = (st[0], st[1])
        known = {k[1] for k in self._windows if k[0] == device_id}
        for cell in known | set(cells_now):
            key = (device_id, cell)
            win = self._windows.get(key)
            if win is None:
                win = self._windows[key] = deque(maxlen=self.cfg.window_frames)
            win.append(cells_now.get(cell, (ABSENT, ABSENT)))
            if cell in by_cell:
                cs = [self._grid_center(p["bbox"]) for p in by_cell[cell]]
                self._centers[key] = (sum(c[0] for c in cs) / len(cs), sum(c[1] for c in cs) / len(cs))
            if all(s == (ABSENT, ABSENT) for s in win):
                del self._windows[key]  # 메모리 상한: 비어 있는 셀은 버림
                self._centers.pop(key, None)
        return assigned

    def _confirmed(self, device_id: str, cell: Cell, person: Dict[str, Any]) -> List[str]:
        win = self._windows.get((device_id, cell))
        if not win:
            return []
        types = []
        for idx, (item, vtype) in enumerate(ITEMS):
            if not self._counts_as_missing(person[item]["status"]):
                continue
            n = sum(1 for s in win if self._counts_as_missing(s[idx]))
            if n >= self.cfg.min_missing_frames:
                types.append(vtype)
        return sorted(types)

    def _prune_emitted(self, now_s: float) -> None:
        w = self.cfg.dedup_window_s
        for k in [k for k, (t, _) in self._emitted.items() if now_s - t >= w and now_s >= t]:
            del self._emitted[k]

    # ------------------------------------------------------------------
    def update(self, edge_frame: Dict[str, Any], inference_result: Dict[str, Any], jpeg: bytes
               ) -> Optional[Dict[str, Any]]:
        """프레임 1장 처리. 위반이 확정되고 dedup 억제되지 않으면 ppe-event dict 를 반환(publisher 가 있으면 spool 게시)."""
        device_id = inference_result["device_id"]
        if inference_result["frame_id"] != edge_frame["frame_id"]:
            raise ValueError("edge_frame/inference_result frame_id mismatch")
        persons = inference_result.get("persons") or []
        cells = self._update_windows(device_id, persons)

        occ, occ_src = occurred_at(edge_frame, self.cfg.time_max_device_skew_ms)
        occ_s = parse_utc(occ).timestamp()
        w = self.cfg.dedup_window_s
        window_start = int(occ_s // w * w) if w > 0 else int(occ_s)
        self._prune_emitted(occ_s)

        violations = []
        pending: List[Tuple[Tuple[str, Cell, str], str]] = []
        frames_considered = 1
        for p in sorted(persons, key=lambda q: q["person_index"]):
            cell = cells[p["person_index"]]
            types = self._confirmed(device_id, cell, p)
            if not types:
                continue
            tkey = ",".join(types)
            dkey = f"{device_id}|pos:r{cell[0]}c{cell[1]}|{tkey}|{window_start}"
            sup_key = (device_id, cell, tkey)
            last = self._emitted.get(sup_key)
            if last is not None and (last[1] == dkey or 0 <= occ_s - last[0] < w):
                continue
            if any(k == sup_key for k, _ in pending):  # 같은 프레임·같은 셀 중복 사람
                continue
            pending.append((sup_key, dkey))
            frames_considered = max(frames_considered, len(self._windows[(device_id, cell)]))
            violations.append({
                "person_index": p["person_index"],
                "track_id": p.get("track_id"),
                "bbox": dict(p["bbox"]),
                "helmet": dict(p["helmet"]),
                "vest": dict(p["vest"]),
                "violation_types": types,
            })
        if not violations:
            return None
        violations = violations[:64]

        event_id = self.id_fn()
        dt = parse_utc(occ)
        img_size = jpeg_size(jpeg)
        if img_size is None:
            img_size = (inference_result["image_size"]["width"], inference_result["image_size"]["height"])
        event = {
            "schema_version": self.cfg.schema_version,
            "event_id": event_id,
            "event_type": "PPE_VIOLATION",
            "device_id": device_id,
            "frame_id": edge_frame["frame_id"],
            "boot_id": edge_frame["boot_id"],
            "frame_seq": edge_frame["frame_seq"],
            "timestamps": {
                "occurred_at_utc": occ,
                "occurred_at_source": occ_src,
                "capture_ts_utc": edge_frame.get("capture_ts_utc"),
                "mcu_uptime_ms": edge_frame["mcu_uptime_ms"],
                "gateway_received_at_utc": edge_frame["gateway_received_at_utc"],
                "gateway_clock_synced": edge_frame["gateway_clock_synced"],
                "decided_at_utc": format_utc(self.now_fn()),
            },
            "violations": violations,
            "image": {
                "image_path": f"images/{dt:%Y}/{dt:%m}/{dt:%d}/{event_id}.jpg",
                "content_type": "image/jpeg",
                "byte_length": len(jpeg),
                "sha256": hashlib.sha256(jpeg).hexdigest(),
                "width": int(img_size[0]),
                "height": int(img_size[1]),
            },
            "model": dict(inference_result["model"]),
            "decision": {
                "policy_version": self.cfg.policy_version,
                "dedup_key": pending[0][1],
                "dedup_window_s": w,
                "frames_considered": frames_considered,
            },
        }
        if self.publisher is not None:
            self.publisher.publish(event, jpeg)  # 실패 시 예외 → dedup 상태 미기록(다음 프레임에서 재시도)
        for sup_key, dkey in pending:
            self._emitted[sup_key] = (occ_s, dkey)
        return event
