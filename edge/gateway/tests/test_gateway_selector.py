from edge.gateway.selector import FrameSelector


def frame(device, person=None, seq=0):
    return {
        "device_id": device,
        "frame_seq": seq,
        "edge_detection": None if person is None else {"person_detected": person},
        "selection": {"reason": "LATEST_FRAME", "queue_depth_at_enqueue": 0, "dropped_since_last_selected": 0},
    }


def test_latest_wins_drops_previous_and_counts():
    s = FrameSelector(max_frames=4, max_bytes=1000)
    assert s.offer(frame("a", seq=1), b"x" * 10).accepted
    r = s.offer(frame("a", seq=2), b"y" * 20)
    assert r.accepted and r.replaced and r.queue_depth == 1
    s.offer(frame("a", seq=3), b"z" * 30)
    st = s.stats()
    assert (st.frames, st.bytes, st.dropped_total) == (1, 30, 2)
    f, jpeg = s.get(timeout=0)
    assert f["frame_seq"] == 3 and jpeg == b"z" * 30
    assert f["selection"]["dropped_since_last_selected"] == 2
    # 꺼낸 뒤에는 0 부터 다시
    s.offer(frame("a", seq=4), b"w")
    assert s.get(timeout=0)[0]["selection"]["dropped_since_last_selected"] == 0


def test_frame_cap_rejects_new_device_but_allows_replacement():
    s = FrameSelector(max_frames=2, max_bytes=1000)
    assert s.offer(frame("a"), b"1").accepted
    assert s.offer(frame("b"), b"1").accepted
    r = s.offer(frame("c"), b"1")
    assert not r.accepted and r.queue_depth == 2
    assert s.offer(frame("a", seq=9), b"2").accepted  # 같은 장치 교체는 자리를 더 차지하지 않음
    assert len(s) == 2


def test_byte_cap():
    s = FrameSelector(max_frames=10, max_bytes=100)
    assert s.offer(frame("a"), b"x" * 60).accepted
    assert not s.offer(frame("b"), b"x" * 41).accepted
    assert s.offer(frame("b"), b"x" * 40).accepted
    # 교체 시에는 이전 프레임 크기를 빼고 계산
    assert s.offer(frame("a"), b"x" * 60).accepted
    assert not s.offer(frame("a"), b"x" * 61).accepted
    assert s.stats().bytes == 100


def test_rejected_offer_leaves_queue_unchanged():
    s = FrameSelector(max_frames=1, max_bytes=100)
    s.offer(frame("a", seq=1), b"x" * 50)
    assert not s.offer(frame("a", seq=2), b"x" * 101).accepted
    f, _ = s.get(timeout=0)
    assert f["frame_seq"] == 1 and f["selection"]["dropped_since_last_selected"] == 0


def test_queue_depth_at_enqueue():
    s = FrameSelector(max_frames=4, max_bytes=1000)
    fa, fb, fa2 = frame("a"), frame("b"), frame("a")
    s.offer(fa, b"1")
    s.offer(fb, b"1")
    s.offer(fa2, b"1")
    assert fa["selection"]["queue_depth_at_enqueue"] == 0
    assert fb["selection"]["queue_depth_at_enqueue"] == 1
    assert fa2["selection"]["queue_depth_at_enqueue"] == 1  # 교체 대상과 자신은 제외


def test_person_frames_first_then_fifo():
    s = FrameSelector(max_frames=4, max_bytes=1000)
    s.offer(frame("a", person=False), b"1")
    s.offer(frame("b"), b"1")
    s.offer(frame("c", person=True), b"1")
    s.offer(frame("d", person=True), b"1")
    order = [s.get(timeout=0)[0]["device_id"] for _ in range(4)]
    assert order == ["c", "d", "a", "b"]
    assert s.get(timeout=0) is None


def test_replacement_keeps_queue_position():
    s = FrameSelector(max_frames=4, max_bytes=1000)
    s.offer(frame("a", seq=1), b"1")
    s.offer(frame("b"), b"1")
    s.offer(frame("a", seq=2), b"1")
    f, _ = s.get(timeout=0)
    assert (f["device_id"], f["frame_seq"]) == ("a", 2)


def test_get_timeout_returns_none():
    assert FrameSelector(1, 1).get(timeout=0.01) is None
