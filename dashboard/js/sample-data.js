// 샘플 데이터 생성기.
// contracts/supabase-event-record.schema.json 형태의 행을 결정적(seed)으로 만든다.
// 증거 이미지는 bbox 위치에 사람을 그린 SVG(data URI)로 대신한다.
(function (PPE) {
  "use strict";

  const DEVICES = ["cam-line-a-01", "cam-line-a-02", "cam-line-b-01", "cam-warehouse-01"];
  const IMAGE_W = 640;
  const IMAGE_H = 480;
  const DEDUP_WINDOW_S = 30;
  const DEDUP_GRID = 4;
  const MODEL = {
    name: "ppe-detector",
    version: "0.1.0",
    input_width: 640,
    input_height: 640,
    runtime: "onnxruntime",
    runtime_version: "1.19.2",
  };

  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function hex(rand, n) {
    let s = "";
    for (let i = 0; i < n; i++) s += Math.floor(rand() * 16).toString(16);
    return s;
  }

  // UUIDv7: 48비트 ms 타임스탬프 + 버전 7 + 변형 비트
  function uuidv7(rand, ms) {
    const ts = Math.floor(ms).toString(16).padStart(12, "0");
    const variant = "89ab"[Math.floor(rand() * 4)];
    return `${ts.slice(0, 8)}-${ts.slice(8, 12)}-7${hex(rand, 3)}-${variant}${hex(rand, 3)}-${hex(rand, 12)}`;
  }

  function iso(ms) {
    return new Date(ms).toISOString(); // 항상 ...Z
  }

  function datePath(ms) {
    const d = new Date(ms);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}/${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())}`;
  }

  function round2(x) {
    return Math.round(x * 100) / 100;
  }

  function pick(rand, arr) {
    return arr[Math.floor(rand() * arr.length)];
  }

  // 근무 시간대(현지 07~19시)에 몰리도록 가중치를 준 발생 시각
  function randomOccurredAt(rand, now, days) {
    for (;;) {
      const ms = now - rand() * days * 86400000;
      const h = new Date(ms).getHours();
      const weight = h >= 7 && h < 19 ? 1 : 0.12;
      if (rand() < weight) return ms;
    }
  }

  function makeItem(rand, missing) {
    if (missing) return { status: "MISSING", confidence: round2(0.55 + rand() * 0.4) };
    if (rand() < 0.15) return { status: "UNKNOWN", confidence: rand() < 0.5 ? null : round2(0.2 + rand() * 0.3) };
    return { status: "WORN", confidence: round2(0.6 + rand() * 0.38) };
  }

  // 프레임 속 사람들. 위반자(violation_types 비어 있지 않음) 최소 1명, 나머지는 준수자일 수 있다.
  function makePeople(rand) {
    const n = rand() < 0.6 ? 1 : rand() < 0.7 ? 2 : 3;
    const slotW = 1 / n;
    const violator = Math.floor(rand() * n);
    const out = [];
    for (let i = 0; i < n; i++) {
      let helmetMissing = false;
      let vestMissing = false;
      if (i === violator || rand() >= 0.4) {
        const r = rand();
        helmetMissing = r < 0.55 || r >= 0.85;
        vestMissing = r >= 0.55;
      }
      const w = Math.min(slotW * 0.75, 0.16 + rand() * 0.12);
      const h = 0.55 + rand() * 0.35;
      const xMin = i * slotW + rand() * (slotW - w);
      const yMin = Math.max(0.02, 0.98 - h - rand() * 0.06);
      const types = [];
      if (helmetMissing) types.push("HELMET_MISSING");
      if (vestMissing) types.push("VEST_MISSING");
      out.push({
        person_index: i,
        track_id: rand() < 0.5 ? null : `trk-${hex(rand, 6)}`,
        bbox: {
          x_min: round2(xMin),
          y_min: round2(yMin),
          x_max: round2(Math.min(1, xMin + w)),
          y_max: round2(Math.min(1, yMin + h)),
        },
        helmet: makeItem(rand, helmetMissing),
        vest: makeItem(rand, vestMissing),
        violation_types: types,
      });
    }
    return out;
  }

  // docs/protocols.md §중복 방지:
  // {device_id}|{track_id 또는 pos:r{행}c{열}}|{정렬된 violation_types를 ,로 연결}|{window_start_epoch_s}
  // pos 는 bbox 중심을 PPE_DEDUP_GRID(4x4) 격자로 양자화(0부터).
  function makeDedupKey(deviceId, v, occurredMs) {
    const cx = (v.bbox.x_min + v.bbox.x_max) / 2;
    const cy = (v.bbox.y_min + v.bbox.y_max) / 2;
    const cell = (x) => Math.min(DEDUP_GRID - 1, Math.floor(x * DEDUP_GRID));
    const who = v.track_id || `pos:r${cell(cy)}c${cell(cx)}`;
    const types = v.violation_types.slice().sort().join(",");
    const windowStart = Math.floor(occurredMs / 1000 / DEDUP_WINDOW_S) * DEDUP_WINDOW_S;
    return `${deviceId}|${who}|${types}|${windowStart}`;
  }

  // 증거 이미지에 그릴 프레임 전체 인원(준수자 포함). 행에는 계약상 위반자만 들어가므로 따로 보관한다.
  const framePeople = new Map();

  // 프레임 1장 → 위반자마다 이벤트 1건(dedup_key 가 사람 1명 기준).
  // 같은 프레임의 이벤트는 frame_id·시각·이미지(sha256)를 공유하고 event_id·violations·dedup_key 만 다르다.
  function makeFrameEvents(rand, occurredMs, deviceId) {
    const sntp = rand() < 0.85;
    const captureMs = occurredMs;
    const gatewayMs = captureMs + 40 + rand() * 160;
    const decidedMs = gatewayMs + 300 + rand() * 1200;
    const occurredUsed = sntp ? captureMs : gatewayMs;
    const frameId = uuidv7(rand, gatewayMs);
    const imageSha = hex(rand, 64);
    const byteLength = 30000 + Math.floor(rand() * 60000);
    const bootId = hex(rand, 8);
    const frameSeq = Math.floor(rand() * 200000);
    const uptime = Math.floor(rand() * 86400000);
    const framesConsidered = 3 + Math.floor(rand() * 3);
    const people = makePeople(rand);
    framePeople.set(frameId, people);

    return people
      .filter((v) => v.violation_types.length)
      .map((v) => {
        const eventId = uuidv7(rand, decidedMs);
        const payload = {
          schema_version: "1.0.0",
          event_id: eventId,
          event_type: "PPE_VIOLATION",
          device_id: deviceId,
          frame_id: frameId,
          boot_id: bootId,
          frame_seq: frameSeq,
          timestamps: {
            occurred_at_utc: iso(occurredUsed),
            occurred_at_source: sntp ? "DEVICE_SNTP" : "GATEWAY_RECEIVED",
            capture_ts_utc: sntp ? iso(captureMs) : null,
            mcu_uptime_ms: uptime,
            gateway_received_at_utc: iso(gatewayMs),
            gateway_clock_synced: true,
            decided_at_utc: iso(decidedMs),
          },
          violations: [v],
          image: {
            image_path: `images/${datePath(occurredUsed)}/${eventId}.jpg`,
            content_type: "image/jpeg",
            byte_length: byteLength,
            sha256: imageSha,
            width: IMAGE_W,
            height: IMAGE_H,
          },
          model: Object.assign({ sha256: "0".repeat(64) }, MODEL),
          decision: {
            policy_version: "0.1.0",
            dedup_key: makeDedupKey(deviceId, v, occurredUsed),
            dedup_window_s: DEDUP_WINDOW_S,
            frames_considered: framesConsidered,
          },
        };
        return {
          event_id: eventId,
          schema_version: "1.0.0",
          device_id: deviceId,
          occurred_at: iso(occurredUsed),
          occurred_at_source: payload.timestamps.occurred_at_source,
          gateway_received_at: iso(gatewayMs),
          decided_at: iso(decidedMs),
          violation_count: 1,
          violation_types: v.violation_types.slice().sort(),
          storage_bucket: "ppe-evidence",
          storage_path: `${deviceId}/${datePath(occurredUsed)}/${eventId}.jpg`,
          image_sha256: imageSha,
          model_name: MODEL.name,
          model_version: MODEL.version,
          event_payload: payload,
          // 서버 생성 컬럼(클라이언트가 업로드하지 않음)
          ingested_at: iso(decidedMs + 2000 + rand() * 20000),
        };
      });
  }

  function generate(opts) {
    const rand = mulberry32(opts.seed);
    // 정시 단위로 고정해 새로고침해도 event_id(딥링크)가 유지되게 한다
    const now = opts.now || Math.floor(Date.now() / 3600000) * 3600000;
    const rows = [];
    while (rows.length < opts.eventCount) {
      const occurred = randomOccurredAt(rand, now, opts.days);
      rows.push(...makeFrameEvents(rand, occurred, pick(rand, DEVICES)));
    }
    rows.length = opts.eventCount;
    rows.sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : a.occurred_at > b.occurred_at ? -1 : a.event_id < b.event_id ? -1 : 1));
    return rows;
  }

  // 실시간 시뮬레이션용 새 프레임 1장의 이벤트들
  function generateLive(rand, now) {
    return makeFrameEvents(rand, now - 1500, pick(rand, DEVICES));
  }

  // ---- 증거 이미지(SVG) ----

  function personSvg(v) {
    const x = v.bbox.x_min * IMAGE_W;
    const y = v.bbox.y_min * IMAGE_H;
    const w = (v.bbox.x_max - v.bbox.x_min) * IMAGE_W;
    const h = (v.bbox.y_max - v.bbox.y_min) * IMAGE_H;
    const cx = x + w / 2;
    const headR = Math.min(w * 0.22, h * 0.09);
    const headCy = y + headR * 1.6;
    const bodyTop = headCy + headR * 1.1;
    const bodyH = h * 0.42;
    const bodyW = w * 0.62;
    const legTop = bodyTop + bodyH;
    const legH = y + h - legTop;
    const skin = "#c99b7b";
    const shirt = "#4b5a6b";
    const parts = [];
    // 다리
    parts.push(`<rect x="${cx - bodyW * 0.42}" y="${legTop}" width="${bodyW * 0.36}" height="${legH}" rx="4" fill="#2f3640"/>`);
    parts.push(`<rect x="${cx + bodyW * 0.06}" y="${legTop}" width="${bodyW * 0.36}" height="${legH}" rx="4" fill="#2f3640"/>`);
    // 몸통
    parts.push(`<rect x="${cx - bodyW / 2}" y="${bodyTop}" width="${bodyW}" height="${bodyH}" rx="${bodyW * 0.18}" fill="${shirt}"/>`);
    if (v.vest.status === "WORN") {
      parts.push(`<rect x="${cx - bodyW / 2}" y="${bodyTop + 2}" width="${bodyW}" height="${bodyH * 0.92}" rx="${bodyW * 0.18}" fill="#f2711c"/>`);
      parts.push(`<rect x="${cx - bodyW / 2}" y="${bodyTop + bodyH * 0.45}" width="${bodyW}" height="${Math.max(3, bodyH * 0.07)}" fill="#e8e8e0"/>`);
    }
    // 머리
    parts.push(`<circle cx="${cx}" cy="${headCy}" r="${headR}" fill="${skin}"/>`);
    if (v.helmet.status === "WORN") {
      parts.push(`<path d="M ${cx - headR * 1.25} ${headCy - headR * 0.1} A ${headR * 1.25} ${headR * 1.25} 0 0 1 ${cx + headR * 1.25} ${headCy - headR * 0.1} Z" fill="#f7c800"/>`);
    } else {
      parts.push(`<path d="M ${cx - headR} ${headCy - headR * 0.2} A ${headR} ${headR} 0 0 1 ${cx + headR} ${headCy - headR * 0.2} Z" fill="#3a2a20"/>`);
    }
    return parts.join("");
  }

  function evidenceSvg(row) {
    const p = row.event_payload;
    const people = (framePeople.get(p.frame_id) || p.violations).map(personSvg).join("");
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${IMAGE_W} ${IMAGE_H}" width="${IMAGE_W}" height="${IMAGE_H}">` +
      `<defs><linearGradient id="w" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8a9097"/><stop offset="1" stop-color="#5d636a"/></linearGradient></defs>` +
      `<rect width="${IMAGE_W}" height="${IMAGE_H * 0.62}" fill="url(#w)"/>` +
      `<rect y="${IMAGE_H * 0.62}" width="${IMAGE_W}" height="${IMAGE_H * 0.38}" fill="#6f6a60"/>` +
      `<rect x="40" y="${IMAGE_H * 0.18}" width="150" height="${IMAGE_H * 0.44}" fill="#747a80"/>` +
      `<rect x="430" y="${IMAGE_H * 0.26}" width="170" height="${IMAGE_H * 0.36}" fill="#7b8187"/>` +
      `<path d="M0 ${IMAGE_H * 0.86} L${IMAGE_W} ${IMAGE_H * 0.86}" stroke="#d9b400" stroke-width="6" stroke-dasharray="28 18"/>` +
      people +
      `<text x="12" y="24" font-family="monospace" font-size="15" fill="#ffffff" opacity="0.85">${row.device_id}  ${p.timestamps.occurred_at_utc}</text>` +
      `</svg>`;
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  }

  PPE.sampleData = { DEVICES, generate, generateLive, evidenceSvg, mulberry32 };
})(window.PPE);
