"""계약 검증 헬퍼(개발/테스트 전용): contracts/*.schema.json 을 $ref 해석 포함으로 로드해 검증.

의존성: jsonschema, referencing (requirements-dev.txt). 런타임 경로에서 import 하지 않는다.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


@lru_cache(maxsize=4)
def _load(contracts_dir: str):
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    schemas: Dict[str, Dict[str, Any]] = {}
    for p in Path(contracts_dir).glob("*.schema.json"):
        schemas[p.name] = json.loads(p.read_text(encoding="utf-8"))
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values()
    )
    validators = {
        name.replace(".schema.json", ""): Draft202012Validator(s, registry=registry, format_checker=FormatChecker())
        for name, s in schemas.items()
    }
    return validators


def contracts_dir() -> str:
    return os.environ.get("CONTRACTS_DIR", str(DEFAULT_CONTRACTS_DIR))


def errors(schema: str, instance: Any) -> List[str]:
    """schema: 'inference-result', 'ppe-event', 'edge-frame' 등. 오류 메시지 목록(빈 목록=통과)."""
    v = _load(contracts_dir())[schema]
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(instance)]


def validate(schema: str, instance: Any) -> None:
    errs = errors(schema, instance)
    if errs:
        raise ValueError(f"{schema} contract violation:\n  " + "\n  ".join(errs))
