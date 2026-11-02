"""contracts/*.schema.json 로더($ref 해석 포함). 런타임 메타데이터 검증과 테스트에서 함께 쓴다.

스키마 위치: CONTRACTS_DIR 환경변수, 없으면 저장소 루트의 contracts/.
컨테이너 이미지에는 contracts/ 디렉터리를 함께 복사해야 한다.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"

# 시작 시 반드시 로드돼야 하는 스키마
REQUIRED_SCHEMAS = ("esp32-frame-metadata", "edge-frame", "gateway-response")


def contracts_dir() -> str:
    return os.environ.get("CONTRACTS_DIR") or str(DEFAULT_CONTRACTS_DIR)


@lru_cache(maxsize=4)
def _validators(directory: str) -> Dict[str, Draft202012Validator]:
    schemas: Dict[str, Dict[str, Any]] = {}
    for p in sorted(Path(directory).glob("*.schema.json")):
        schemas[p.name[: -len(".schema.json")]] = json.loads(p.read_text(encoding="utf-8"))
    missing = [n for n in REQUIRED_SCHEMAS if n not in schemas]
    if missing:
        raise FileNotFoundError(f"contract schemas not found in {directory!r}: {missing} (set CONTRACTS_DIR)")
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())
    out = {}
    for name, s in schemas.items():
        Draft202012Validator.check_schema(s)
        out[name] = Draft202012Validator(s, registry=registry, format_checker=FormatChecker())
    return out


def load() -> None:
    """시작 시 호출: 스키마를 읽지 못하면 예외."""
    _validators(contracts_dir())


def errors(schema: str, instance: Any) -> List[str]:
    """오류 메시지 목록(빈 목록 = 통과). 경로가 짧은 순으로 정렬."""
    v = _validators(contracts_dir())[schema]
    errs = sorted(v.iter_errors(instance), key=lambda e: (len(e.absolute_path), list(map(str, e.absolute_path))))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errs]
