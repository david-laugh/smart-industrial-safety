"""사람-PPE 연관과 사람별 helmet/vest 상태(WORN/MISSING/UNKNOWN) 판정.

순수 함수(입력: 픽셀 좌표 박스)라서 합성 박스로 단위 테스트할 수 있다.

규칙 (값은 InferenceConfig 로 조정, 모델 카드에 기록):
1. 판정 불가 사람: person_confidence < person_min_confidence 또는 박스 높이 < 최소 높이
   (min_person_box_px 가 있으면 그 px, 없으면 min_person_box_ratio × 이미지 높이)
   → helmet/vest 모두 UNKNOWN, confidence=null. PPE 박스 할당 경쟁에도 참여하지 않는다
   (저신뢰 중복 사람 박스가 실제 작업자의 PPE 박스를 가져가 MISSING 이 되는 것을 막기 위함).
2. 영역: 사람 박스 높이 기준 위에서부터 helmet_region(기본 0~25%), vest_region(기본 20~70%), 가로는 사람 박스 전체.
3. 연관: PPE 박스 중심이 영역 안 AND IoA(PPE∩영역 / PPE 면적) ≥ assoc_min_ioa.
   PPE 박스 하나는 조건을 만족하는 판정 가능 사람 중 IoA 가 가장 큰 1명에게만 할당
   (동률 → PPE 중심과 사람 박스 가로 중심의 거리(사람 폭 대비)가 작은 쪽 → 교차 면적 → person_confidence → person_index).
4. 상태(항목별):
   - 착용 클래스 최고 점수 ≥ worn_min, 미착용 클래스 최고 점수 ≥ missing_min 둘 다 → 점수 높은 쪽(완전 동률은 UNKNOWN)
   - 하나만 → 그 상태, confidence = 그 점수
   - 둘 다 임계값 미만:
       * 임계값 미만이라도 착용 클래스 검출이 연관돼 있으면 → UNKNOWN(애매함, 보수적)
       * 부위 영역이 이미지 경계에 닿지 않고(가림/잘림 없음) → MISSING,
         confidence = person_confidence (부재 근거이므로 사람 검출 신뢰도를 사용)
       * 영역이 이미지 경계에 닿음 → UNKNOWN. 부위별로 판단한다:
           helmet: 위쪽 또는 좌/우가 잘리면 UNKNOWN (아래쪽 잘림은 무관)
           vest  : 아래쪽 또는 좌/우가 잘리면 UNKNOWN (위쪽만 잘린 경우는 정상 판정)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]

WORN, MISSING, UNKNOWN = "WORN", "MISSING", "UNKNOWN"

ITEM_ROLES = {"helmet": ("helmet", "no_helmet"), "vest": ("vest", "no_vest")}


@dataclass(frozen=True)
class PersonBox:
    xyxy: Box
    score: float


@dataclass(frozen=True)
class PPEBox:
    role: str          # helmet | no_helmet | vest | no_vest
    xyxy: Box
    score: float


@dataclass(frozen=True)
class AssocParams:
    person_min_confidence: float = 0.5
    worn_min_confidence: float = 0.5
    missing_min_confidence: float = 0.5
    min_person_box_ratio: float = 0.25
    min_person_box_px: Optional[float] = None
    helmet_region: Tuple[float, float] = (0.0, 0.25)
    vest_region: Tuple[float, float] = (0.2, 0.7)
    assoc_min_ioa: float = 0.5
    edge_margin_px: float = 2.0

    @classmethod
    def from_config(cls, cfg) -> "AssocParams":
        return cls(
            person_min_confidence=cfg.person_min_confidence,
            worn_min_confidence=cfg.ppe_worn_min_confidence,
            missing_min_confidence=cfg.ppe_missing_min_confidence,
            min_person_box_ratio=cfg.min_person_box_ratio,
            min_person_box_px=cfg.min_person_box_px,
            helmet_region=cfg.helmet_region,
            vest_region=cfg.vest_region,
            assoc_min_ioa=cfg.assoc_min_ioa,
            edge_margin_px=cfg.edge_margin_px,
        )


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a: Box, b: Box) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def region_box(person: Box, rng: Tuple[float, float]) -> Box:
    x1, y1, x2, y2 = person
    h = y2 - y1
    return (x1, y1 + rng[0] * h, x2, y1 + rng[1] * h)


def _center_in(b: Box, r: Box) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return r[0] <= cx <= r[2] and r[1] <= cy <= r[3]


def min_box_height(prm: AssocParams, img_h: int) -> float:
    if prm.min_person_box_px is not None:
        return float(prm.min_person_box_px)
    return prm.min_person_box_ratio * img_h


def is_judgeable(p: PersonBox, prm: AssocParams, img_h: int) -> bool:
    return p.score >= prm.person_min_confidence and (p.xyxy[3] - p.xyxy[1]) >= min_box_height(prm, img_h)


def _item_visible(item: str, person: Box, img_w: int, img_h: int, prm: AssocParams) -> bool:
    """검출이 없을 때 MISSING 으로 단정할 만큼 부위 영역이 온전히 보이는지(이미지 경계에 닿지 않음)."""
    m = prm.edge_margin_px
    x1, y1, x2, y2 = person
    if x1 <= m or x2 >= img_w - m:          # 좌/우로 잘림 → 부위 일부가 프레임 밖일 수 있음
        return False
    if item == "helmet" and y1 <= m:         # 위로 잘림(머리 잘림)
        return False
    if item == "vest" and y2 >= img_h - m:   # 아래로 잘림 → 박스 높이 기준 몸통 영역 불확실
        return False
    return True


def assign_ppe(persons: Sequence[PersonBox], ppe: Sequence[PPEBox], prm: AssocParams, img_h: int
               ) -> Dict[int, List[PPEBox]]:
    """PPE 박스를 판정 가능한 사람 1명에게만 할당. 반환: {person_idx: [PPEBox]}"""
    out: Dict[int, List[PPEBox]] = {i: [] for i in range(len(persons))}
    judgeable = [i for i, p in enumerate(persons) if is_judgeable(p, prm, img_h)]
    for d in ppe:
        item = "helmet" if d.role in ITEM_ROLES["helmet"] else "vest" if d.role in ITEM_ROLES["vest"] else None
        if item is None:
            continue
        rng = prm.helmet_region if item == "helmet" else prm.vest_region
        a = _area(d.xyxy)
        if a <= 0:
            continue
        best_key, best_i = None, None
        for i in judgeable:
            r = region_box(persons[i].xyxy, rng)
            if not _center_in(d.xyxy, r):
                continue
            ioa = _inter(d.xyxy, r) / a
            if ioa < prm.assoc_min_ioa:
                continue
            pb = persons[i].xyxy
            # IoA 동점이면 PPE 중심이 사람 박스 가로 중심에 더 가까운 사람(앞뒤로 겹친 사람의 머리가
            # 두 사람 안전모 영역에 모두 IoA 1.0 으로 들어가는 경우, 사람 점수가 아닌 위치로 결정)
            rel = abs((d.xyxy[0] + d.xyxy[2]) / 2 - (pb[0] + pb[2]) / 2) / max(pb[2] - pb[0], 1e-6)
            key = (round(ioa, 6), -rel, _inter(d.xyxy, pb), persons[i].score, -i)
            if best_key is None or key > best_key:
                best_key, best_i = key, i
        if best_i is not None:
            out[best_i].append(d)
    return out


def item_status(item: str, person: PersonBox, assigned: Sequence[PPEBox], img_w: int, img_h: int,
                prm: AssocParams) -> Dict[str, Optional[float]]:
    worn_role, miss_role = ITEM_ROLES[item]
    worn = max((d.score for d in assigned if d.role == worn_role), default=None)
    miss = max((d.score for d in assigned if d.role == miss_role), default=None)
    worn_ok = worn is not None and worn >= prm.worn_min_confidence
    miss_ok = miss is not None and miss >= prm.missing_min_confidence
    if worn_ok and miss_ok:
        if worn > miss:
            return {"status": WORN, "confidence": _c(worn)}
        if miss > worn:
            return {"status": MISSING, "confidence": _c(miss)}
        return {"status": UNKNOWN, "confidence": None}
    if worn_ok:
        return {"status": WORN, "confidence": _c(worn)}
    if miss_ok:
        return {"status": MISSING, "confidence": _c(miss)}
    if worn is not None:  # 임계값 미만 착용 검출 → 애매
        return {"status": UNKNOWN, "confidence": None}
    if _item_visible(item, person.xyxy, img_w, img_h, prm):
        return {"status": MISSING, "confidence": _c(person.score)}
    return {"status": UNKNOWN, "confidence": None}


def _c(x: float) -> float:
    return round(min(1.0, max(0.0, float(x))), 4)


def associate(persons: Sequence[PersonBox], ppe: Sequence[PPEBox], img_w: int, img_h: int,
              prm: AssocParams) -> List[Dict]:
    """사람별 결과(dict) 리스트. bbox 는 0~1 정규화(원본 기준, 좌상단 원점)."""
    assigned = assign_ppe(persons, ppe, prm, img_h)
    results = []
    for i, p in enumerate(persons):
        if is_judgeable(p, prm, img_h):
            helmet = item_status("helmet", p, assigned[i], img_w, img_h, prm)
            vest = item_status("vest", p, assigned[i], img_w, img_h, prm)
        else:
            helmet = {"status": UNKNOWN, "confidence": None}
            vest = {"status": UNKNOWN, "confidence": None}
        results.append({
            "person_index": i,
            "track_id": None,
            "bbox": normalize_box(p.xyxy, img_w, img_h),
            "person_confidence": _c(p.score),
            "helmet": helmet,
            "vest": vest,
        })
    return results


def normalize_box(b: Box, w: int, h: int) -> Dict[str, float]:
    def n(v: float, s: int) -> float:
        return round(min(1.0, max(0.0, v / s)), 6)
    x1, y1, x2, y2 = n(b[0], w), n(b[1], h), n(b[2], w), n(b[3], h)
    return {"x_min": min(x1, x2), "y_min": min(y1, y2), "x_max": max(x1, x2), "y_max": max(y1, y2)}
