"""spool 원자적 게시 (docs/protocols.md §4 spool 게시 절차).

1) JPEG  → {root}/spool/tmp/ 에 쓰기 + fsync → {root}/images/yyyy/mm/dd/{event_id}.jpg 로 rename
2) JSON  → {root}/spool/tmp/ 에 쓰기 + fsync → {root}/spool/incoming/{event_id}.json 로 rename
cloud-sync 는 incoming/ 의 JSON 이 보일 때만 수집하므로, JSON 이 보이는 시점에는 JPEG 가 이미 완성돼 있다.
tmp/ 와 대상은 같은 파일시스템(EVENT_DATA_ROOT 아래)이어야 rename 이 원자적이다.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional


def _fsync_dir(path: str) -> None:
    if os.name != "posix":  # Windows 는 디렉터리 fsync 미지원
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_fsync(path: str, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


class SpoolPublisher:
    def __init__(self, data_root: str, on_step: Optional[Callable[[str, str], None]] = None) -> None:
        self.root = data_root
        self.tmp = os.path.join(data_root, "spool", "tmp")
        self.incoming = os.path.join(data_root, "spool", "incoming")
        self.on_step = on_step  # 테스트용 훅: (step, path)
        os.makedirs(self.tmp, exist_ok=True)
        os.makedirs(self.incoming, exist_ok=True)

    def _step(self, name: str, path: str) -> None:
        if self.on_step:
            self.on_step(name, path)

    def publish(self, event: Dict[str, Any], jpeg: bytes) -> Dict[str, str]:
        event_id = event["event_id"]
        rel_img = event["image"]["image_path"]
        if rel_img.startswith("/") or ".." in rel_img:
            raise ValueError(f"image_path must be relative without '..': {rel_img}")
        img_dst = os.path.join(self.root, *rel_img.split("/"))
        json_dst = os.path.join(self.incoming, f"{event_id}.json")
        if os.path.exists(json_dst):
            raise FileExistsError(json_dst)  # event_id 는 1회 생성 — 덮어쓰지 않는다

        # 1) JPEG
        os.makedirs(os.path.dirname(img_dst), exist_ok=True)
        img_tmp = os.path.join(self.tmp, f"{event_id}.jpg.part")
        _write_fsync(img_tmp, jpeg)
        self._step("jpeg_tmp_written", img_tmp)
        os.replace(img_tmp, img_dst)
        _fsync_dir(os.path.dirname(img_dst))
        self._step("jpeg_published", img_dst)

        # 2) JSON
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        json_tmp = os.path.join(self.tmp, f"{event_id}.json.part")
        _write_fsync(json_tmp, body)
        self._step("json_tmp_written", json_tmp)
        os.replace(json_tmp, json_dst)
        _fsync_dir(self.incoming)
        self._step("json_published", json_dst)
        return {"image": img_dst, "json": json_dst}
