"""실행: `python -m edge.gateway` (저장소 루트가 PYTHONPATH 에 있어야 함).

설정·스키마·모델을 uvicorn 시작 전에 모두 로드한다. 하나라도 실패하면 종료 코드 2로 즉시 끝난다.
"""
from __future__ import annotations

import logging
import sys

from .config import ConfigError, GatewayConfig


def main() -> int:
    try:
        config = GatewayConfig.from_env()
    except ConfigError as e:
        print(f"gateway: invalid configuration: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("edge.gateway")

    try:
        from . import schemas

        schemas.load()
        from edge.ppe_decision.pipeline import PPEPipeline

        pipeline = PPEPipeline.from_env()
    except Exception:
        log.exception("gateway: startup failed")
        return 2

    import uvicorn

    from .app import create_app

    app = create_app(config, pipeline)
    log.info(
        "gateway listening on %s:%d (devices=%d, queue=%d frames/%d bytes)",
        config.bind_addr, config.port, len(config.device_tokens), config.queue_max_frames, config.queue_max_bytes,
    )
    uvicorn.run(app, host=config.bind_addr, port=config.port, workers=1, log_level=config.log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
