// 대시보드 런타임 설정.
// 이 파일에는 비밀 값을 넣지 않는다. 서버가 /js/config.local.js 로 환경변수 값을 덮어쓴다(비밀 값 없음).
window.PPE = window.PPE || {};

window.PPE.config = Object.assign(
  {
    // "sample" = 브라우저에서 생성하는 샘플 데이터
    // "supabase" = 대시보드 서버 /api 경유로 실제 ppe_events / ppe-evidence 조회(키는 서버에만)
    dataSource: "sample",

    signedUrlExpiresSec: 120,
    liveIntervalMs: 15000, // supabase 모드 실시간 폴링 주기

    pageSize: 20,

    // 샘플 모드 전용
    sample: {
      seed: 20261102,
      days: 30,
      eventCount: 180,
      liveIntervalMs: 15000,
    },
  },
  window.PPE_LOCAL_CONFIG || {}
);
