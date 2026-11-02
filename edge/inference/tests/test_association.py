"""사람-PPE 연관/상태 판정 — 합성 박스 입력(현재 영상으로 검증되지 않는 경로 포함)."""
from edge.inference.association import (MISSING, UNKNOWN, WORN, AssocParams, PersonBox, PPEBox,
                                        assign_ppe, associate)

W, H = 640, 480
PRM = AssocParams(person_min_confidence=0.5, worn_min_confidence=0.5, missing_min_confidence=0.5,
                  min_person_box_px=160.0, helmet_region=(0.0, 0.25), vest_region=(0.2, 0.7),
                  assoc_min_ioa=0.5, edge_margin_px=2.0)

# 이미지 안에 온전히 보이는 사람: x 100~220, y 40~400 (높이 360)
FULL = (100.0, 40.0, 220.0, 400.0)
HELMET_BOX = (135.0, 42.0, 185.0, 100.0)     # 상단 0~25% (y 40~130)
VEST_BOX = (110.0, 130.0, 210.0, 280.0)      # 20~70% (y 112~292)


def one(persons, ppe, prm=PRM):
    return associate(persons, ppe, W, H, prm)


def test_visible_parts_without_detection_are_missing():
    r = one([PersonBox(FULL, 0.9)], [])[0]
    assert r["helmet"] == {"status": MISSING, "confidence": 0.9}
    assert r["vest"] == {"status": MISSING, "confidence": 0.9}


def test_worn_detections():
    r = one([PersonBox(FULL, 0.9)], [PPEBox("helmet", HELMET_BOX, 0.8), PPEBox("vest", VEST_BOX, 0.7)])[0]
    assert r["helmet"] == {"status": WORN, "confidence": 0.8}
    assert r["vest"] == {"status": WORN, "confidence": 0.7}


def test_missing_class_detection():
    r = one([PersonBox(FULL, 0.9)], [PPEBox("no_helmet", HELMET_BOX, 0.6), PPEBox("no_vest", VEST_BOX, 0.55)])[0]
    assert r["helmet"] == {"status": MISSING, "confidence": 0.6}
    assert r["vest"] == {"status": MISSING, "confidence": 0.55}


def test_head_cut_at_top_is_unknown_but_vest_judged():
    p = (100.0, 0.0, 220.0, 360.0)  # 위쪽만 잘림, 조끼 영역은 이미지 안
    r = one([PersonBox(p, 0.9)], [])[0]
    assert r["helmet"] == {"status": UNKNOWN, "confidence": None}
    assert r["vest"] == {"status": MISSING, "confidence": 0.9}


def test_top_and_bottom_cut_both_unknown():
    p = (100.0, 0.0, 220.0, 480.0)
    r = one([PersonBox(p, 0.9)], [])[0]
    assert r["helmet"]["status"] == UNKNOWN and r["vest"]["status"] == UNKNOWN


def test_min_box_ratio_default_and_px_override():
    from edge.inference.config import InferenceConfig
    cfg = InferenceConfig.from_env({})
    assert cfg.min_person_box_ratio == 0.33 and cfg.min_person_box_px is None
    prm = AssocParams.from_config(cfg)
    p150 = PersonBox((100.0, 40.0, 220.0, 190.0), 0.9)   # 150px < 0.33*480=158.4
    p170 = PersonBox((100.0, 40.0, 220.0, 210.0), 0.9)   # 170px
    assert one([p150], [], prm)[0]["helmet"]["status"] == UNKNOWN
    assert one([p170], [], prm)[0]["helmet"]["status"] == MISSING
    cfg = InferenceConfig.from_env({"INFERENCE_MIN_PERSON_BOX_PX": "100", "INFERENCE_MIN_PERSON_BOX_RATIO": "0.9"})
    assert one([p150], [], AssocParams.from_config(cfg))[0]["helmet"]["status"] == MISSING  # px 우선


def test_torso_out_of_frame_is_unknown():
    p = (100.0, 200.0, 220.0, 480.0)  # 아래로 잘림(몸통 일부 프레임 밖)
    r = one([PersonBox(p, 0.9)], [])[0]
    assert r["vest"] == {"status": UNKNOWN, "confidence": None}
    assert r["helmet"]["status"] == MISSING  # 머리는 온전히 보임


def test_side_cut_is_unknown():
    p = (0.0, 40.0, 120.0, 400.0)
    r = one([PersonBox(p, 0.9)], [])[0]
    assert r["helmet"]["status"] == UNKNOWN and r["vest"]["status"] == UNKNOWN


def test_detection_wins_over_cut_rule():
    """잘린 사람이라도 검출 근거가 있으면 그 상태를 쓴다."""
    p = (100.0, 0.0, 220.0, 360.0)
    r = one([PersonBox(p, 0.9)], [PPEBox("helmet", (135.0, 0.0, 185.0, 60.0), 0.8)])[0]
    assert r["helmet"]["status"] == WORN


def test_small_person_is_unknown_with_null_confidence():
    small = (300.0, 100.0, 330.0, 230.0)  # 높이 130 < 160
    r = one([PersonBox(small, 0.95)], [PPEBox("no_helmet", (305.0, 101.0, 325.0, 125.0), 0.9)])[0]
    assert r["helmet"] == {"status": UNKNOWN, "confidence": None}
    assert r["vest"] == {"status": UNKNOWN, "confidence": None}


def test_low_confidence_person_is_unknown():
    r = one([PersonBox(FULL, 0.3)], [])[0]
    assert r["helmet"]["status"] == UNKNOWN and r["helmet"]["confidence"] is None
    assert r["vest"]["status"] == UNKNOWN and r["vest"]["confidence"] is None
    assert r["person_confidence"] == 0.3


def test_worn_and_missing_both_associated_higher_wins():
    r = one([PersonBox(FULL, 0.9)], [PPEBox("helmet", HELMET_BOX, 0.62), PPEBox("no_helmet", HELMET_BOX, 0.81),
                                     PPEBox("vest", VEST_BOX, 0.9), PPEBox("no_vest", VEST_BOX, 0.6)])[0]
    assert r["helmet"] == {"status": MISSING, "confidence": 0.81}
    assert r["vest"] == {"status": WORN, "confidence": 0.9}


def test_exact_tie_is_unknown():
    r = one([PersonBox(FULL, 0.9)], [PPEBox("helmet", HELMET_BOX, 0.7), PPEBox("no_helmet", HELMET_BOX, 0.7)])[0]
    assert r["helmet"] == {"status": UNKNOWN, "confidence": None}


def test_below_threshold_worn_is_unknown_not_missing():
    r = one([PersonBox(FULL, 0.9)], [PPEBox("helmet", HELMET_BOX, 0.3)])[0]
    assert r["helmet"] == {"status": UNKNOWN, "confidence": None}


def test_ppe_outside_region_not_associated():
    feet = (120.0, 350.0, 200.0, 398.0)  # 하단 — 안전모/조끼 영역 밖
    r = one([PersonBox(FULL, 0.9)], [PPEBox("helmet", feet, 0.9), PPEBox("vest", feet, 0.9)])[0]
    assert r["helmet"]["status"] == MISSING and r["vest"]["status"] == MISSING


def test_overlapping_persons_ppe_assigned_to_one_only():
    a = PersonBox((100.0, 50.0, 250.0, 450.0), 0.9)
    b = PersonBox((180.0, 60.0, 330.0, 460.0), 0.9)
    helmet = PPEBox("helmet", (190.0, 55.0, 240.0, 110.0), 0.85)  # 두 사람 영역 모두 중심 포함, A 와 겹침이 더 큼
    assigned = assign_ppe([a, b], [helmet], PRM, H)
    assert assigned[0] == [helmet] and assigned[1] == []
    ra, rb = one([a, b], [helmet])
    assert ra["helmet"]["status"] == WORN
    assert rb["helmet"]["status"] == MISSING


def test_low_confidence_duplicate_does_not_steal_ppe():
    real = PersonBox(FULL, 0.85)
    dup = PersonBox((120.0, 45.0, 220.0, 400.0), 0.29)  # 저신뢰 중복 박스
    ppe = [PPEBox("helmet", HELMET_BOX, 0.8), PPEBox("vest", VEST_BOX, 0.9)]
    r_real, r_dup = one([real, dup], ppe)
    assert r_real["helmet"]["status"] == WORN and r_real["vest"]["status"] == WORN
    assert r_dup["helmet"]["status"] == UNKNOWN


def test_bbox_normalized_and_schema_shape():
    r = one([PersonBox((-5.0, 40.0, 700.0, 500.0), 0.9)], [])[0]
    b = r["bbox"]
    assert 0 <= b["x_min"] <= b["x_max"] <= 1 and 0 <= b["y_min"] <= b["y_max"] <= 1
    assert r["track_id"] is None and r["person_index"] == 0
