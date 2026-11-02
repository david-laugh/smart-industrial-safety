"""통합 진입점: PPEPipeline.process(edge_frame, jpeg) -> list[ppe_event]

내부: infer(edge.inference) → PPEDecision.update → spool 게시(위반자마다 이벤트 1건).
모델 세션(InferenceSession 1개)과 decision 상태(평활화 창, dedup)를 인스턴스 수명 동안 유지한다.
프로세스당 1개만 만들어 재사용할 것. process() 는 내부 잠금으로 직렬화된다.

    from edge.ppe_decision.pipeline import PPEPipeline
    pipeline = PPEPipeline.from_env()            # 환경변수로 설정
    events = pipeline.process(edge_frame, jpeg)  # 위반 없으면 []
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Mapping, Optional

from edge.inference.api import InferenceEngine
from edge.inference.config import InferenceConfig

from .config import DecisionConfig
from .decision import PPEDecision


class PPEPipeline:
    def __init__(self, engine: InferenceEngine, decision: PPEDecision) -> None:
        self.engine = engine
        self.decision = decision
        self.last_inference_result: Optional[Dict[str, Any]] = None  # 메트릭/디버그용(timing 등)
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, publish: bool = True) -> "PPEPipeline":
        engine = InferenceEngine(InferenceConfig.from_env(env))
        decision = PPEDecision(DecisionConfig.from_env(env), publish=publish)
        return cls(engine, decision)

    def process(self, edge_frame: Dict[str, Any], jpeg: bytes) -> List[Dict[str, Any]]:
        """프레임 1장 처리. 게시된 ppe-event 리스트(위반자마다 1건)를 반환.

        예외: edge.inference.api.InvalidImageError(JPEG 디코드 실패), OSError(spool 쓰기 실패).
        spool 실패 시 해당 이벤트는 dedup 에 기록되지 않아 다음 프레임에서 다시 발행된다.
        """
        with self._lock:
            result = self.engine.infer(edge_frame, jpeg)
            self.last_inference_result = result
            return self.decision.update(edge_frame, result, jpeg)
