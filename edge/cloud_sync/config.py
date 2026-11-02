"""환경변수 로드. 이름은 infra/docker/.env.example 기준, (추가) 표시는 C 가 새로 제안한 변수."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


class ConfigError(ValueError):
    pass


def _float(env: Mapping[str, str], name: str, default: float, minimum: float = 0) -> float:
    raw = (env.get(name) or "").strip()
    try:
        v = float(raw) if raw else default
    except ValueError:
        raise ConfigError(f"{name} 는 숫자여야 합니다: {raw!r}") from None
    if v < minimum:
        raise ConfigError(f"{name} 는 {minimum} 이상이어야 합니다: {v}")
    return v


def _int_or_none(env: Mapping[str, str], name: str) -> Optional[int]:
    raw = (env.get(name) or "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        raise ConfigError(f"{name} 는 정수여야 합니다: {raw!r}") from None
    if v < 1:
        raise ConfigError(f"{name} 는 1 이상이어야 합니다: {v}")
    return v


def read_service_key(env: Mapping[str, str]) -> str:
    """SUPABASE_SERVICE_KEY_FILE(권장, 0600/Docker secret) → 없으면 SUPABASE_SERVICE_KEY(개발용)."""
    path = (env.get("SUPABASE_SERVICE_KEY_FILE") or "").strip()
    if path and Path(path).is_file():
        key = Path(path).read_text(encoding="utf-8").strip()
    else:
        key = (env.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not key:
        raise ConfigError("SUPABASE_SERVICE_KEY_FILE(파일이 있어야 함) 또는 SUPABASE_SERVICE_KEY 가 필요합니다.")
    return key


@dataclass(frozen=True)
class SyncConfig:
    data_root: str
    db_path: str
    supabase_url: str
    service_key: str = field(repr=False)  # 로그·repr 에 나오지 않게
    events_table: str = "ppe_events"
    storage_bucket: str = "ppe-evidence"
    concurrency: int = 1
    retry_base_s: float = 2.0
    retry_max_s: float = 300.0
    max_attempts: Optional[int] = None  # None = 무제한
    http_timeout_s: float = 15.0
    poll_interval_s: float = 2.0  # (추가) 할 일이 없을 때 대기
    auth_pause_s: float = 300.0  # (추가) 401/403/설정 오류 시 업로드 전체 일시 중지 시간
    batch_size: int = 50  # (추가) 한 번에 꺼내는 outbox 항목 수
    retention_days: float = 7.0
    store_max_bytes: Optional[int] = None
    sqlite_synchronous: str = "FULL"  # (추가) 전원 차단 테스트 후 조정

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "SyncConfig":
        env = os.environ if env is None else env
        root = (env.get("EVENT_DATA_ROOT") or "/data").strip()
        url = (env.get("SUPABASE_URL") or "").strip().rstrip("/")
        local = url.startswith(("http://localhost", "http://127.0.0.1"))
        if not (url.startswith("https://") or local):
            raise ConfigError("SUPABASE_URL 은 https:// 로 시작해야 합니다(로컬 개발만 http://localhost 허용).")
        concurrency = int(_float(env, "SYNC_CONCURRENCY", 1, minimum=1))
        sync_mode = (env.get("EVENT_STORE_SQLITE_SYNCHRONOUS") or "FULL").strip().upper()
        if sync_mode not in ("FULL", "NORMAL", "EXTRA"):
            raise ConfigError(f"EVENT_STORE_SQLITE_SYNCHRONOUS 는 FULL/NORMAL/EXTRA 중 하나: {sync_mode}")
        return cls(
            data_root=root,
            db_path=(env.get("EVENT_STORE_DB_PATH") or "").strip() or os.path.join(root, "outbox.db"),
            supabase_url=url,
            service_key=read_service_key(env),
            events_table=(env.get("SUPABASE_EVENTS_TABLE") or "ppe_events").strip(),
            storage_bucket=(env.get("SUPABASE_STORAGE_BUCKET") or "ppe-evidence").strip(),
            concurrency=concurrency,
            retry_base_s=_float(env, "SYNC_RETRY_BASE_S", 2.0),
            retry_max_s=_float(env, "SYNC_RETRY_MAX_S", 300.0),
            max_attempts=_int_or_none(env, "SYNC_MAX_ATTEMPTS"),
            http_timeout_s=_float(env, "SYNC_HTTP_TIMEOUT_S", 15.0, minimum=1),
            poll_interval_s=_float(env, "SYNC_POLL_INTERVAL_S", 2.0, minimum=0.1),
            auth_pause_s=_float(env, "SYNC_AUTH_PAUSE_S", 300.0, minimum=1),
            batch_size=int(_float(env, "SYNC_BATCH_SIZE", 50, minimum=1)),
            retention_days=_float(env, "EVENT_LOCAL_RETENTION_DAYS", 7.0),
            store_max_bytes=_int_or_none(env, "EVENT_STORE_MAX_BYTES"),
            sqlite_synchronous=sync_mode,
        )
