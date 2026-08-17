-- Run this once in the Supabase SQL editor before enabling SUPABASE_URL and SUPABASE_KEY.
-- The app uses this generic JSONB envelope so the Streamlit UI and a persistent collector
-- share exactly the same event schema without exposing credentials to the browser.
create table if not exists public.scanner_events (
  id text primary key,
  kind text not null,
  created_at timestamptz not null,
  payload jsonb not null default '{}'::jsonb
);

-- Existing projects may have the original narrow CHECK constraint. Safely replace it.
alter table public.scanner_events drop constraint if exists scanner_events_kind_check;
alter table public.scanner_events add constraint scanner_events_kind_check check (
  kind in (
    'validation_case',
    'manual_trade',
    'minute_bar_v51',
    'signal_event_v51',
    'cycle_state_v51',
    'calibration_snapshot_v51'
  )
);

create index if not exists scanner_events_kind_created_at_idx
  on public.scanner_events (kind, created_at desc);
create index if not exists scanner_events_cycle_lookup_idx
  on public.scanner_events ((payload->>'market'), (payload->>'symbol'), (payload->>'trade_date'))
  where kind = 'cycle_state_v51';
create index if not exists scanner_events_bar_lookup_idx
  on public.scanner_events ((payload->>'market'), (payload->>'symbol'), (payload->>'bar_at'))
  where kind = 'minute_bar_v51';

alter table public.scanner_events enable row level security;

-- This app uses a server-side secret. Never expose a service-role key in browser code.
-- If using an anon key, create a narrowly scoped authenticated policy before deployment.
