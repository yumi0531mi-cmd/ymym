-- Run this once in the Supabase SQL editor before enabling SUPABASE_URL and SUPABASE_KEY.
create table if not exists public.scanner_events (
  id text primary key,
  kind text not null check (kind in ('validation_case', 'manual_trade')),
  created_at timestamptz not null,
  payload jsonb not null default '{}'::jsonb
);

create index if not exists scanner_events_kind_created_at_idx
  on public.scanner_events (kind, created_at desc);

alter table public.scanner_events enable row level security;

-- This app uses a server-side secret. Do not expose a service-role key in browser code.
-- If using an anon key instead, create a dedicated authenticated policy before deployment.
