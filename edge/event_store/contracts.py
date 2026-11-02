"""contracts/*.schema.json 을 $ref 해석 포함으로 로드한다(spool 수집 시 ppe-event 검증용).

스키마 위치: CONTRACTS_DIR (기본: 저장소 contracts/). 컨테이너에는 contracts/ 를 함께 복사해야 한다.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, List

DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


def contracts_dir() -> str:
    return os.environ.get("CONTRACTS_DIR", str(DEFAULT_CONTRACTS_DIR))


@lru_cache(maxsize=4)
def _validators(path: str):
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    schemas = [json.loads(p.read_text(encoding="utf-8")) for p in Path(path).glob("*.schema.json")]
    if not schemas:
        raise FileNotFoundError(f"계약 스키마가 없습니다: {path}")
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    return {
        s["$id"].rsplit("/", 1)[1].replace(".schema.json", ""): Draft202012Validator(
            s, registry=registry, format_checker=FormatChecker()
        )
        for s in schemas
    }


def errors(schema: str, instance: Any) -> List[str]:
    """schema: 'ppe-event', 'supabase-event-record' 등. 빈 목록 = 통과."""
    v = _validators(contracts_dir())[schema]
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(instance)]
