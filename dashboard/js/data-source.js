// 데이터 소스 어댑터. UI(app.js)는 이 인터페이스만 사용한다.
//
//   auth.getSession() / auth.signIn(email, password) / auth.signOut()
//   listDevices()                         → string[]
//   listEvents(filter, { offset, limit }) → { rows, total }
//   listStats(filter)                     → 요약/차트용 경량 행 [{ occurred_at, device_id, violation_types, violation_count }]
//   getEvent(eventId)                     → row | null
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

  // ---------------- Mock ----------------

  function createMockSource() {
    const rows = PPE.mockData.generate(cfg.mock);
    const liveRand = PPE.mockData.mulberry32(cfg.mock.seed ^ 0x5eed);
    const SESSION_KEY = "ppe-dashboard-mock-session";

    function readSession() {
      try {
        const raw = sessionStorage.getItem(SESSION_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch (e) {
        return null;
      }
    }

    let session = readSession();

    return {
      kind: "mock",
      auth: {
        async getSession() {
          return session;
        },
        async signIn(email) {
          if (!email) throw new Error("이메일을 입력하세요.");
          session = { user: { email } };
          try {
            sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
          } catch (e) {
            /* 저장 불가 환경에서도 동작 */
          }
          return delay(session);
        },
        async signOut() {
          session = null;
          try {
            sessionStorage.removeItem(SESSION_KEY);
          } catch (e) {
            /* noop */
          }
        },
      },
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
            }))
        );
      },
      async getEvent(id) {
        return delay(rows.find((r) => r.event_id === id) || null);
      },
      async getSignedImageUrl(row) {
        return delay({
          url: PPE.mockData.evidenceSvg(row),
          expiresAt: new Date(Date.now() + cfg.signedUrlExpiresSec * 1000).toISOString(),
        });
      },
      subscribe(onInsert) {
        const timer = setInterval(() => {
          const row = PPE.mockData.generateLive(liveRand, Date.now());
          rows.unshift(row);
          onInsert(row);
        }, cfg.mock.liveIntervalMs);
        return () => clearInterval(timer);
      },
    };
  }

  // ---------------- Supabase ----------------
  // cloud/README.md 설계(ppe_events select, ppe-evidence Signed URL, authenticated RLS)를 전제로 한다.

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
    const sb = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey);

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
      auth: {
        async getSession() {
          const { data } = await sb.auth.getSession();
          return data.session;
        },
        async signIn(email, password) {
          const res = check(await sb.auth.signInWithPassword({ email, password }));
          return res.data.session;
        },
        async signOut() {
          await sb.auth.signOut();
        },
      },
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
        let q = sb.from(cfg.table).select("occurred_at,device_id,violation_types,violation_count");
        q = applyFilter(q, f).order("occurred_at", { ascending: false }).limit(5000);
        const res = check(await q);
        return res.data;
      },
      async getEvent(id) {
        const res = check(await sb.from(cfg.table).select("*").eq("event_id", id).maybeSingle());
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
    return cfg.dataSource === "supabase" ? createSupabaseSource() : createMockSource();
  };
})(window.PPE);
