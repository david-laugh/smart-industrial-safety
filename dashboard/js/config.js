// 대시보드 런타임 설정.
// 이 파일에는 비밀 값을 넣지 않는다. 실제 Supabase 값은 config.local.js(커밋 금지)에서 덮어쓴다.
window.PPE = window.PPE || {};

window.PPE.config = Object.assign(
  {
    // "mock" = 브라우저 내 가데이터, "supabase" = 실제 ppe_events / ppe-evidence 조회
    dataSource: "mock",

    // dataSource === "supabase" 일 때만 사용. anon key만 허용(service_role 금지).
    supabaseUrl: "",
    supabaseAnonKey: "",
    supabaseJsUrl: "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js",

    table: "ppe_events",
    storageBucket: "ppe-evidence",
    signedUrlExpiresSec: 120,

    pageSize: 20,

    // 목업 모드 전용
    mock: {
      seed: 20261102,
      days: 30,
      eventCount: 180,
      liveIntervalMs: 15000,
    },
  },
  window.PPE_LOCAL_CONFIG || {}
);
