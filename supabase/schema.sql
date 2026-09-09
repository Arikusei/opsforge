-- Схема PostgreSQL / Supabase для промышленного контура OpsForge.
-- Локальное демо использует SQLite; этот файл — целевой backend.

create extension if not exists vector;
create extension if not exists pg_trgm;

create table if not exists products (
  id bigint generated always as identity primary key,
  sku text unique not null,
  name text not null,
  brand text not null,
  category text not null,
  summary text not null,
  tags text default '',
  in_stock int default 0
);

create table if not exists knowledge_chunks (
  id bigint generated always as identity primary key,
  source text not null,
  title text not null,
  content text not null,
  embedding vector(1536)
);

create index if not exists knowledge_chunks_embedding_idx
  on knowledge_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 50);

create index if not exists knowledge_chunks_content_trgm
  on knowledge_chunks using gin (content gin_trgm_ops);

create table if not exists leads (
  id bigint generated always as identity primary key,
  company text not null,
  contact text default '',
  phone text default '',
  email text default '',
  source text default 'site',
  stage text default 'new',
  industry text default '',
  need text default '',
  tags text default '',
  owner text default 'Отдел продаж',
  crm_id text default '',
  score double precision default 0,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists call_events (
  id bigint generated always as identity primary key,
  external_id text default '',
  caller text not null,
  callee text default '+70000000000',
  status text default 'missed',
  duration int default 0,
  summary text default '',
  lead_id bigint references leads(id),
  created_at timestamptz default now()
);

create table if not exists workflow_runs (
  id bigint generated always as identity primary key,
  name text not null,
  trigger text not null,
  status text default 'success',
  payload jsonb default '{}'::jsonb,
  result jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

create table if not exists integration_events (
  id bigint generated always as identity primary key,
  system text not null,
  action text not null,
  payload jsonb default '{}'::jsonb,
  ok boolean default true,
  created_at timestamptz default now()
);

-- RLS-заготовка: сервисная роль API пишет, сотрудники читают сделки своего отдела.
alter table leads enable row level security;
create policy "sales_read_leads" on leads for select using (true);
