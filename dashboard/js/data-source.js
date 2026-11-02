// 데이터 소스 어댑터. UI(app.js)는 이 인터페이스만 사용한다.
//
//   listDevices()                        → string[]
//   listEvents(filter, { offset, limit }) → { rows, total }
//   listStats(filter)                     → 요약/차트용 경량 행 [{ occurred_at, device_id, violation_types, violation_count, frame_id }]
//   getEvent(eventId)                     → row | null
//   listFrameEvents(frameId)              → 같은 프레임(event_payload.frame_id)에서 나온 이벤트들
//   getSignedImageUrl(row)                → { url, expiresAt }
//   subscribe(onInsert)                   → unsubscribe()
//
// filter = { from: ISO, to: ISO, deviceIds: string[], violationTypes: string[] }
//   deviceIds / violationTypes 가 비어 있으면 전체. violationTypes 는 "하나라도 포함"(overlaps).
(function (PPE) {
  "use strict";

  const cfg = PPE.config;

  function matches(row, f) {
    if (f.from && row.occurred_at < f.from) return false;
    if (f.to && row.occurred_at >= f.to) return false;
    if (f.deviceIds.length && !f.deviceIds.includes(row.device_id)) return false;
    if (f.violationTypes.length && !row.violation_types.some((t) => f.violationTypes.includes(t))) return false;
    return true;
  }

  function delay(v) {
    return new Promise((r) => setTimeout(() => r(v), 120 + Math.random() * 180));
  }

  // ---------------- Sample ----------------

  function createSampleSource() {
    const rows = PPE.sampleData.generate(cfg.sample);
    const liveRand = PPE.sampleData.mulberry32(cfg.sample.seed ^ 0x5eed);

    return {
      kind: "sample",
      async listDevices() {
        return delay(Array.from(new Set(rows.map((r) => r.device_id))).sort());
      },
      async listEvents(f, page) {
        const hit = rows.filter((r) => matches(r, f));
        return delay({ rows: hit.slice(page.offset, page.offset + page.limit), total: hit.length });
      },
      async listStats(f) {
        return delay(
          rows
            .filter((r) => matches(r, f))
            .map((r) => ({
              occurred_at: r.occurred_at,
              device_id: r.device_id,
              violation_types: r.violation_types,
              violation_count: r.violation_count,
              frame_id: r.event_payload.frame_id,
            }))
        );
      },
      async getEvent(id) {
        return delay(rows.find((r) => r.event_id === id) || null);
      },
      async listFrameEvents(frameId) {
        return delay(rows.filter((r) => r.event_payload.frame_id === frameId));
      },
      async getSignedImageUrl(row) {
        return delay({
          url: PPE.sampleData.evidenceSvg(row),
          expiresAt: new Date(Date.now() + cfg.signedUrlExpiresSec * 1000).toISOString(),
        });
      },
      subscribe(onInsert) {
        const timer = setInterval(() => {
          for (const row of PPE.sampleData.generateLive(liveRand, Date.now())) {
            rows.unshift(row);
            onInsert(row);
          }
        }, cfg.sample.liveIntervalMs);
        return () => clearInterval(timer);
      },
    };
  }

  // ---------------- Supabase (대시보드 서버 경유) ----------------
  // 브라우저는 Supabase 키를 갖지 않는다. 대시보드 서버(server/supabase_api.py)가 secret 키로 대신 조회한다.
  // 접근 토큰은 httpOnly 쿠키로 서버가 확인한다(SYS-SEC-01). 개발용 테이블/버킷: dashboard/seed/schema.sql

  async function api(path, params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) {
      for (const item of [].concat(v)) if (item !== undefined && item !== null && item !== "") qs.append(k, item);
    }
    const res = await fetch("api/" + path + (qs.toString() ? "?" + qs : ""), { credentials: "same-origin" });
    if (res.status === 401) throw new Error("접근 토큰이 필요합니다. 관리자에게 받은 주소(/?token=…)로 다시 여세요.");
    if (res.status === 404) return null;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || "서버 오류 " + res.status);
    return body;
  }

  function filterParams(f) {
    return { from: f.from, to: f.to, device: f.deviceIds, type: f.violationTypes };
  }

  function createSupabaseSource() {
    return {
      kind: "supabase",
      listDevices: () => api("devices"),
      listEvents: (f, page) => api("events", { ...filterParams(f), offset: page.offset, limit: page.limit }),
      listStats: (f) => api("stats", filterParams(f)),
      getEvent: (id) => api("events/" + encodeURIComponent(id)),
      listFrameEvents: async (frameId) => (await api("frames/" + encodeURIComponent(frameId))) || [],
      getSignedImageUrl: (row) => api("events/" + encodeURIComponent(row.event_id) + "/image"),
      subscribe(onInsert) {
        // Realtime 대신 서버 폴링(ingested_at 커서)
        let cursor = null;
        let stopped = false;
        const tick = async () => {
          try {
            const res = await api("live", cursor ? { after: cursor } : {});
            cursor = res.cursor;
            if (!stopped) res.rows.forEach(onInsert);
          } catch (err) {
            console.warn("실시간 확인 실패:", err.message);
          }
        };
        tick();
        const timer = setInterval(tick, cfg.liveIntervalMs);
        return () => {
          stopped = true;
          clearInterval(timer);
        };
      },
    };
  }

  PPE.createDataSource = async function () {
    return cfg.dataSource === "supabase" ? createSupabaseSource() : createSampleSource();
  };
})(window.PPE);
