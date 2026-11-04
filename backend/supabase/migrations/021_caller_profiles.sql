-- 021_caller_profiles.sql: what the receptionist remembers about a phone caller across calls (services/caller_profiles.py,
-- routers/callers.py). One row per (owner, E.164 number); `data` is the profile (display name, preferred language, last
-- outcome, last booking, follow-ups, a short summary, the consent note). Rows are deleted by the owner (DELETE
-- /api/callers/{number}) or at startup when `last_call_at` is older than CALLER_PROFILE_RETENTION_DAYS (default 180).
-- New table only: no existing table is touched. The backend uses the service-role key and always filters by user_id; the
-- policy below keeps direct client access owner-only too. Idempotent.

create table if not exists public.caller_profiles (
  user_id uuid not null references auth.users (id) on delete cascade,
  number text not null,
  last_call_at timestamptz,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (user_id, number)
);

create index if not exists caller_profiles_recent_idx on public.caller_profiles (user_id, last_call_at desc);

alter table public.caller_profiles enable row level security;
drop policy if exists caller_profiles_owner on public.caller_profiles;
create policy caller_profiles_owner on public.caller_profiles
  for all using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
