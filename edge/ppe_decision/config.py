"""PPE Decision 설정: 환경변수(PPE_*, TIME_MAX_DEVICE_SKEW_MS, EVENT_DATA_ROOT) + 기본값.

빈 문자열 환경변수는 미설정으로 본다. 정책을 바꾸면 PPE_POLICY_VERSION 을 올린다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple


def _get(env: Mapping[str, str], name: str) -> Optional[str]:
    v = env.get(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def parse_grid(v: str) -> Tuple[int, int]:
    """'{열}x{행}' (가로x세로, 예: 4x4, 6x4) → (rows, cols)."""
    c, _, r = v.lower().partition("x")
    cols, rows = int(c), int(r)
    if cols < 1 or rows < 1:
        raise ValueError(f"PPE_DEDUP_GRID must be like 4x4, got {v!r}")
    return rows, cols


def _bool(v: Optional[str], default: bool) -> bool:
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class DecisionConfig:
    policy_version: str = "0.1.0"
    window_frames: int = 5
    min_missing_frames: int = 3
    unknown_is_violation: bool = False
    dedup_window_s: int = 30
    grid_rows: int = 4
    grid_cols: int = 4
    cell_sticky_dist: float = 0.5   # 격자 단위. 0 이면 sticky 비활성(순수 양자화)
    time_max_device_skew_ms: int = 2000
    data_root: str = "/data"
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not 1 <= self.min_missing_frames <= self.window_frames:
            raise ValueError("require 1 <= PPE_DECISION_MIN_MISSING_FRAMES <= PPE_DECISION_WINDOW_FRAMES")
        if not 0 <= self.dedup_window_s <= 3600:
            raise ValueError("PPE_DEDUP_WINDOW_S must be within 0..3600 (ppe-event schema)")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "DecisionConfig":
        env = os.environ if env is None else env
        d = cls()
        grid = _get(env, "PPE_DEDUP_GRID")
        rows, cols = parse_grid(grid) if grid else (d.grid_rows, d.grid_cols)
        return cls(
            policy_version=_get(env, "PPE_POLICY_VERSION") or d.policy_version,
            window_frames=int(_get(env, "PPE_DECISION_WINDOW_FRAMES") or d.window_frames),
            min_missing_frames=int(_get(env, "PPE_DECISION_MIN_MISSING_FRAMES") or d.min_missing_frames),
            unknown_is_violation=_bool(_get(env, "PPE_UNKNOWN_IS_VIOLATION"), d.unknown_is_violation),
            dedup_window_s=int(_get(env, "PPE_DEDUP_WINDOW_S") or d.dedup_window_s),
            grid_rows=rows,
            grid_cols=cols,
            cell_sticky_dist=float(_get(env, "PPE_CELL_STICKY_DIST") or d.cell_sticky_dist),
            time_max_device_skew_ms=int(_get(env, "TIME_MAX_DEVICE_SKEW_MS") or d.time_max_device_skew_ms),
            data_root=_get(env, "EVENT_DATA_ROOT") or d.data_root,
            schema_version=_get(env, "PPE_EVENT_SCHEMA_VERSION") or d.schema_version,
        )
