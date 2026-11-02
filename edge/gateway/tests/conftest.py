import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (ROOT, HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from edge.gateway.config import GatewayConfig  # noqa: E402
from gateway_testlib import TOKENS  # noqa: E402


@pytest.fixture
def tokens_file(tmp_path):
    p = tmp_path / "device_tokens"
    p.write_text("# test tokens\n" + "".join(f"{d}={t}\n" for d, t in TOKENS.items()), encoding="utf-8")
    return p


@pytest.fixture
def make_config(tokens_file):
    def _make(**overrides):
        env = {"GATEWAY_DEVICE_TOKENS_FILE": str(tokens_file)}
        env.update({k: str(v) for k, v in overrides.items()})
        return GatewayConfig.from_env(env)

    return _make
