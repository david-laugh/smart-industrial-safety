"""가데이터를 Supabase dev 프로젝트에 업로드한다 — cloud-sync 와 같은 규칙(docs/protocols.md §5).

    cd dashboard
    .venv/Scripts/python -m seed --count 60 --days 7            # 업로드
    .venv/Scripts/python -m seed --count 10 --out seed-out      # 업로드 없이 파일로만 생성

업로드 순서: Storage(x-upsert: false, 409 시 해시 비교) → ppe_events upsert(on_conflict=event_id, ignore-duplicates).
쓰기 키: .env 의 SUPABASE_SERVICE_KEY (SUPABASE_SERVICE_KEY_FILE 이 있으면 그 파일 우선).
대시보드 서버도 같은 키로 조회하지만 브라우저로는 내려보내지 않는다.
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .generate import BUCKET, generate, validator

DASHBOARD_DIR = Path(__file__).resolve().parents[1]
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class SeedError(RuntimeError):
    pass


def _service_key():
    path = os.environ.get("SUPABASE_SERVICE_KEY_FILE", "").strip()
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not key:
        raise SeedError(".env 에 SUPABASE_SERVICE_KEY(dev 프로젝트의 secret 키)가 필요합니다.")
    return key


def _request(client, method, url, *, attempts=5, base_s=1.0, max_s=30.0, **kw):
    """docs/protocols.md §재시도 규칙: 408/429/5xx·네트워크 오류는 백오프(Retry-After 우선), 401/403 은 즉시 중단."""
    for attempt in range(attempts):
        try:
            resp = client.request(method, url, **kw)
        except httpx.TransportError as e:
            if attempt == attempts - 1:
                raise SeedError(f"네트워크 오류: {e}") from e
            time.sleep(min(max_s, base_s * 2**attempt) * random.uniform(0.5, 1.0))
            continue
        if resp.status_code in (401, 403):
            raise SeedError(f"{resp.status_code} 인증/권한 오류 — 키와 RLS를 확인하세요: {resp.text[:200]}")
        if resp.status_code in RETRY_STATUS and attempt < attempts - 1:
            retry_after = resp.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(max_s, base_s * 2**attempt)
            time.sleep(delay * (1 if retry_after else random.uniform(0.5, 1.0)))
            continue
        return resp
    return resp


def _is_conflict(resp):
    if resp.status_code == 409:
        return True
    # Supabase Storage 는 중복 객체를 400 + {"statusCode":"409"} 로 돌려주기도 한다
    try:
        return resp.status_code == 400 and str(resp.json().get("statusCode")) == "409"
    except ValueError:
        return False


def upload_image(client, base, row, jpeg):
    url = f"{base}/storage/v1/object/{row['storage_bucket']}/{row['storage_path']}"
    resp = _request(client, "POST", url, content=jpeg, headers={"Content-Type": "image/jpeg", "x-upsert": "false"})
    if resp.is_success:
        return "uploaded"
    if _is_conflict(resp):
        existing = _request(client, "GET", url)
        if existing.is_success and hashlib.sha256(existing.content).hexdigest() == row["image_sha256"]:
            return "exists"
        raise SeedError(f"Storage 객체가 이미 있고 해시가 다릅니다: {row['storage_path']}")
    raise SeedError(f"Storage 업로드 실패 {resp.status_code}: {resp.text[:200]}")


def upsert_row(client, base, table, row):
    resp = _request(
        client,
        "POST",
        f"{base}/rest/v1/{table}",
        params={"on_conflict": "event_id"},
        json=row,
        headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
    )
    if not resp.is_success:
        raise SeedError(f"ppe_events upsert 실패 {resp.status_code}: {resp.text[:300]}")


def write_files(events, out):
    out = Path(out)
    for row, jpeg in events:
        img = out / "storage" / row["storage_bucket"] / row["storage_path"]
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(jpeg)
    (out / "ppe_events.json").write_text(json.dumps([r for r, _ in events], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(events)}건 → {out}/ppe_events.json, {out}/storage/{BUCKET}/…")


def upload(events, base, key, table, transport=None):
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    done = {"uploaded": 0, "exists": 0}
    with httpx.Client(headers=headers, timeout=15, transport=transport) as client:
        for i, (row, jpeg) in enumerate(events, 1):  # occurred_at 오래된 순
            done[upload_image(client, base, row, jpeg)] += 1  # 1) 이미지 먼저
            upsert_row(client, base, table, row)  # 2) 행은 나중(이미지 없는 행이 보이지 않게)
            print(f"\r{i}/{len(events)} {row['device_id']} {row['occurred_at']}", end="", flush=True)
    print(f"\n완료: 이미지 신규 {done['uploaded']} / 기존 {done['exists']}, 행 {len(events)}건 upsert(중복은 무시)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m seed", description="Supabase dev 프로젝트용 PPE 이벤트 가데이터")
    p.add_argument("--count", type=int, default=60, help="이벤트 수 (기본 60)")
    p.add_argument("--days", type=float, default=7, help="최근 며칠에 분포 (기본 7)")
    p.add_argument("--seed", type=int, default=None, help="난수 seed (같은 값이면 같은 내용)")
    p.add_argument("--out", help="업로드하지 않고 이 디렉터리에 JSON/JPEG 로 저장")
    args = p.parse_args(argv)

    load_dotenv(DASHBOARD_DIR / ".env", override=False)
    events = generate(args.count, args.days, args.seed if args.seed is not None else random.randrange(2**32))

    # CON-01: 업로드 전 계약 스키마 100% 통과 확인
    v = validator()
    errors = [f"{r['event_id']}: {e.json_path}: {e.message}" for r, _ in events for e in v.iter_errors(r)]
    if errors:
        raise SeedError("계약 스키마 검증 실패:\n" + "\n".join(errors[:20]))

    if args.out:
        write_files(events, args.out)
        return
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not base.startswith("https://"):
        raise SeedError("SUPABASE_URL(https://…)이 필요합니다.")
    upload(events, base, _service_key(), os.environ.get("SUPABASE_EVENTS_TABLE", "ppe_events"))


if __name__ == "__main__":
    try:
        main()
    except SeedError as e:
        print(f"\n오류: {e}", file=sys.stderr)
        sys.exit(1)
