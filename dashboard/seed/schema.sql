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
-- anon / authenticated 에는 아무 정책도 두지 않는다 → publishable 키로는 행·객체 모두 접근 불가(cloud/README.md §3).
-- 조회는 대시보드 서버, 업로드는 cloud-sync·seed 가 secret(service_role) 키로 한다(RLS 우회, 키는 서버에만).
alter table public.ppe_events enable row level security;

-- 이전 버전 스크립트가 만든 anon 조회 정책 제거
drop policy if exists "dashboard read ppe_events" on public.ppe_events;
drop policy if exists "dashboard sign ppe-evidence" on storage.objects;

-- 4. Realtime 불필요 ------------------------------------------------------------
-- 대시보드 실시간 토글은 서버 폴링(ingested_at)으로 동작한다. 이전에 추가했다면 제거.
do $$
begin
  if exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'ppe_events'
  ) then
    alter publication supabase_realtime drop table public.ppe_events;
  end if;
end $$;

-- 5. 실시간 폴링용 인덱스 -------------------------------------------------------
create index if not exists ppe_events_ingested_at_idx on public.ppe_events (ingested_at);
