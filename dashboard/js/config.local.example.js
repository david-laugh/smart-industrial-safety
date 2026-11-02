// 이 파일을 config.local.js 로 복사해 실제 값을 넣는다. config.local.js 는 커밋하지 않는다.
// anon key만 사용한다. service_role 키를 절대 넣지 않는다.
window.PPE_LOCAL_CONFIG = {
  dataSource: "supabase",
  supabaseUrl: "https://<project-ref>.supabase.co",
  supabaseAnonKey: "<anon-key>",
};
