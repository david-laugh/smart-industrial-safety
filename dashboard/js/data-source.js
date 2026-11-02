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

  // ---------------- Supabase ----------------
  // cloud/README.md 설계(ppe_events select, ppe-evidence Signed URL)를 전제로 한다.
  // 개발용 테이블/버킷/RLS: dashboard/seed/schema.sql

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error("스크립트를 불러오지 못했습니다: " + src));
      document.head.appendChild(s);
    });
  }

  async function createSupabaseSource() {
    if (!cfg.supabaseUrl || !cfg.supabaseAnonKey) {
      throw new Error("supabaseUrl / supabaseAnonKey 가 설정되지 않았습니다 (config.local.js).");
    }
    if (!window.supabase) await loadScript(cfg.supabaseJsUrl);
    // 로그인 세션 없이 토큰(anon key)만으로 요청한다. 접근 허용 여부는 서버 RLS가 토큰의 role로 판단.
    const sb = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey, {
      auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    });

    function applyFilter(q, f) {
      if (f.from) q = q.gte("occurred_at", f.from);
      if (f.to) q = q.lt("occurred_at", f.to);
      if (f.deviceIds.length) q = q.in("device_id", f.deviceIds);
      if (f.violationTypes.length) q = q.overlaps("violation_types", f.violationTypes);
      return q;
    }

    function check(res) {
      if (res.error) throw new Error(res.error.message);
      return res;
    }

    return {
      kind: "supabase",
      async listDevices() {
        // TODO(C): 장치 목록 뷰/테이블이 생기면 교체. 지금은 최근 행에서 추출.
        const res = check(await sb.from(cfg.table).select("device_id").order("occurred_at", { ascending: false }).limit(1000));
        return Array.from(new Set(res.data.map((r) => r.device_id))).sort();
      },
      async listEvents(f, page) {
        let q = sb.from(cfg.table).select("*", { count: "exact" });
        q = applyFilter(q, f).order("occurred_at", { ascending: false }).range(page.offset, page.offset + page.limit - 1);
        const res = check(await q);
        return { rows: res.data, total: res.count || 0 };
      },
      async listStats(f) {
        // TODO(C): 행이 많아지면 집계 RPC/뷰로 교체. 지금은 경량 컬럼만 최대 5000행 조회.
        let q = sb.from(cfg.table).select("occurred_at,device_id,violation_types,violation_count,frame_id:event_payload->>frame_id");
        q = applyFilter(q, f).order("occurred_at", { ascending: false }).limit(5000);
        const res = check(await q);
        return res.data;
      },
      async getEvent(id) {
        const res = check(await sb.from(cfg.table).select("*").eq("event_id", id).maybeSingle());
        return res.data;
      },
      async listFrameEvents(frameId) {
        // TODO(C): 행이 많아지면 (event_payload->>'frame_id') 식 인덱스 추가 검토
        const res = check(await sb.from(cfg.table).select("*").eq("event_payload->>frame_id", frameId).limit(64));
        return res.data;
      },
      async getSignedImageUrl(row) {
        const res = check(await sb.storage.from(row.storage_bucket).createSignedUrl(row.storage_path, cfg.signedUrlExpiresSec));
        return {
          url: res.data.signedUrl,
          expiresAt: new Date(Date.now() + cfg.signedUrlExpiresSec * 1000).toISOString(),
        };
      },
      subscribe(onInsert) {
        const ch = sb
          .channel("ppe_events_inserts")
          .on("postgres_changes", { event: "INSERT", schema: "public", table: cfg.table }, (msg) => onInsert(msg.new))
          .subscribe();
        return () => sb.removeChannel(ch);
      },
    };
  }

  PPE.createDataSource = async function () {
    return cfg.dataSource === "supabase" ? createSupabaseSource() : createSampleSource();
  };
})(window.PPE);
