"""Gateway 설정: 환경변수(GATEWAY_*)를 읽고 잘못된 값이면 시작 시점에 즉시 실패한다.

토큰 파일 형식(GATEWAY_DEVICE_TOKENS_FILE): 한 줄에 `device_id=token`. 빈 줄과 `#` 주석은 무시.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional

# common.schema.json#/$defs/deviceId 와 같은 패턴
_DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
# esp32-frame-metadata.schema.json image.byte_length 상한. 이보다 큰 이미지 상한은 스키마와 모순.
SCHEMA_MAX_IMAGE_BYTES = 1048576

_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class ConfigError(ValueError):
    """잘못된 설정. 메시지에 변수명을 포함한다."""


@dataclass(frozen=True)
class GatewayConfig:
    device_tokens: Dict[str, str] = field(repr=False)  # device_id -> token (repr 에 노출하지 않음)
    bind_addr: str = "0.0.0.0"
    port: int = 8080
    max_request_bytes: int = 1179648
    max_image_bytes: int = 1048576
    queue_max_frames: int = 4
    queue_max_bytes: int = 4194304
    retry_after_ms: int = 1000
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "GatewayConfig":
        env = os.environ if env is None else env

        def get(name: str, default: str) -> str:
            v = env.get(name)
            return default if v is None or v.strip() == "" else v.strip()

        def get_int(name: str, default: int, minimum: int, maximum: Optional[int] = None) -> int:
            raw = get(name, str(default))
            try:
                value = int(raw)
            except ValueError:
                raise ConfigError(f"{name}: integer expected, got {raw!r}") from None
            if value < minimum or (maximum is not None and value > maximum):
                rng = f">= {minimum}" if maximum is None else f"in [{minimum}, {maximum}]"
                raise ConfigError(f"{name}: must be {rng}, got {value}")
            return value

        tokens_file = env.get("GATEWAY_DEVICE_TOKENS_FILE", "").strip()
        if not tokens_file:
            raise ConfigError("GATEWAY_DEVICE_TOKENS_FILE: required")
        tokens = load_device_tokens(tokens_file)

        bind_addr = get("GATEWAY_BIND_ADDR", "0.0.0.0")
        port = get_int("GATEWAY_PORT", 8080, 1, 65535)
        max_image = get_int("GATEWAY_MAX_IMAGE_BYTES", 1048576, 1, SCHEMA_MAX_IMAGE_BYTES)
        max_request = get_int("GATEWAY_MAX_REQUEST_BYTES", 1179648, 1)
        q_frames = get_int("GATEWAY_QUEUE_MAX_FRAMES", 4, 1)
        q_bytes = get_int("GATEWAY_QUEUE_MAX_BYTES", 4194304, 1)
        retry = get_int("GATEWAY_RETRY_AFTER_MS", 1000, 0)
        log_level = get("LOG_LEVEL", "INFO").upper()

        if max_request < max_image:
            raise ConfigError(
                f"GATEWAY_MAX_REQUEST_BYTES ({max_request}) must be >= GATEWAY_MAX_IMAGE_BYTES ({max_image})"
            )
        if q_bytes < max_image:
            raise ConfigError(
                f"GATEWAY_QUEUE_MAX_BYTES ({q_bytes}) must be >= GATEWAY_MAX_IMAGE_BYTES ({max_image})"
            )
        if log_level not in _LOG_LEVELS:
            raise ConfigError(f"LOG_LEVEL: one of {sorted(_LOG_LEVELS)} expected, got {log_level!r}")

        return cls(
            device_tokens=tokens,
            bind_addr=bind_addr,
            port=port,
            max_request_bytes=max_request,
            max_image_bytes=max_image,
            queue_max_frames=q_frames,
            queue_max_bytes=q_bytes,
            retry_after_ms=retry,
            log_level=log_level,
        )


def load_device_tokens(path: str) -> Dict[str, str]:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"GATEWAY_DEVICE_TOKENS_FILE: cannot read {path!r}: {e.strerror or e}") from None
    return parse_device_tokens(text)


def parse_device_tokens(text: str) -> Dict[str, str]:
    """`device_id=token` 줄 단위. 오류 메시지에 토큰 값은 넣지 않는다."""
    tokens: Dict[str, str] = {}
    seen_tokens: Dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        device_id, sep, token = line.partition("=")
        device_id, token = device_id.strip(), token.strip()
        where = f"GATEWAY_DEVICE_TOKENS_FILE line {lineno}"
        if not sep:
            raise ConfigError(f"{where}: expected 'device_id=token'")
        if not _DEVICE_ID_RE.match(device_id):
            raise ConfigError(f"{where}: invalid device_id {device_id!r}")
        if not token:
            raise ConfigError(f"{where}: empty token for {device_id!r}")
        if any(c.isspace() for c in token):
            raise ConfigError(f"{where}: token for {device_id!r} contains whitespace")
        if device_id in tokens:
            raise ConfigError(f"{where}: duplicate device_id {device_id!r}")
        if token in seen_tokens:
            raise ConfigError(f"{where}: token for {device_id!r} is already used by {seen_tokens[token]!r}")
        tokens[device_id] = token
        seen_tokens[token] = device_id
    if not tokens:
        raise ConfigError("GATEWAY_DEVICE_TOKENS_FILE: no device tokens defined")
    return tokens
