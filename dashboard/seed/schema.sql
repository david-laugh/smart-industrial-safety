-- =============================================================================
-- 개발용(Supabase dev 프로젝트 전용) 테이블·버킷·RLS — 대시보드 I-5 통합 테스트용
-- 기준: cloud/README.md §1~3, contracts/supabase-event-record.schema.json
-- 정식 마이그레이션은 cloud/ 에 둔다(구현 단계). 운영(prod) 프로젝트에 실행하지 않는다.
-- Supabase 대시보드 → SQL Editor 에 붙여 넣어 실행. 여러 번 실행해도 안전(idempotent).
-- =============================================================================

-- 1. 테이블 ------------------------------------------------------------------
create table if not exists public.ppe_events (
  event_id            uuid        primary key,
  schema_version      text        not null,
  device_id           text        not null,
  occurred_at         timestamptz not null,
  occurred_at_source  text        not null check (occurred_at_source in ('DEVICE_SNTP', 'GATEWAY_RECEIVED')),
  gateway_received_at timestamptz not null,
  decided_at          timestamptz not null,
  violation_count     smallint    not null check (violation_count > 0),
  violation_types     text[]      not null,
  storage_bucket      text        not null,
  storage_path        text        not null unique,
  image_sha256        text        not null,
  model_name          text        not null,
  model_version       text        not null,
  event_payload       jsonb       not null,
  ingested_at         timestamptz not null default now()  -- 서버 생성, 클라이언트가 보내지 않음
);

create index if not exists ppe_events_occurred_at_idx on public.ppe_events (occurred_at desc);
create index if not exists ppe_events_device_occurred_idx on public.ppe_events (device_id, occurred_at desc);
create index if not exists ppe_events_violation_types_gin on public.ppe_events using gin (violation_types);

-- 2. 비공개 버킷 ---------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('ppe-evidence', 'ppe-evidence', false, 1048576, array['image/jpeg'])
on conflict (id) do update
  set public = false, file_size_limit = 1048576, allowed_mime_types = array['image/jpeg'];

-- 3. RLS ----------------------------------------------------------------------
-- 대시보드는 로그인 없이 토큰(anon key)으로 요청한다. 토큰 없는 요청은 PostgREST/Storage 단계에서 거부된다.
-- 쓰기 정책은 두지 않는다 → 업로드는 RLS를 우회하는 service_role(cloud-sync, seed 스크립트)만 가능.
alter table public.ppe_events enable row level security;

drop policy if exists "dashboard read ppe_events" on public.ppe_events;
create policy "dashboard read ppe_events"
  on public.ppe_events for select
  to anon, authenticated
  using (true);

-- Signed URL 발급(createSignedUrl)에는 객체 select 권한이 필요하다. 버킷은 계속 비공개.
drop policy if exists "dashboard sign ppe-evidence" on storage.objects;
create policy "dashboard sign ppe-evidence"
  on storage.objects for select
  to anon, authenticated
  using (bucket_id = 'ppe-evidence');

-- 4. Realtime(선택: 대시보드 "실시간" 토글) -------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'ppe_events'
  ) then
    alter publication supabase_realtime add table public.ppe_events;
  end if;
end $$;
