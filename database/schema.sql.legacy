-- BHYT / viện phí document store for Supabase PostgreSQL + pgvector.
-- Apply once in Supabase SQL Editor.
-- Knowledge-graph nodes and edges are stored in Neo4j (see neo4j/), not here.

create schema if not exists extensions;
create extension if not exists vector with schema extensions;
alter extension vector set schema extensions;

create table if not exists datasets (
    dataset_id text primary key,
    fingerprint text not null unique,
    status text not null check (status in ('staging', 'active', 'failed', 'superseded')),
    manifest jsonb not null,
    collection_name text not null unique,
    created_at timestamptz not null default now(),
    published_at timestamptz,
    failure_reason text
);

create table if not exists dataset_state (
    singleton boolean primary key default true check (singleton),
    active_dataset_id text references datasets(dataset_id),
    updated_at timestamptz not null default now()
);
insert into dataset_state (singleton) values (true) on conflict (singleton) do nothing;

create table if not exists documents (
    dataset_id text not null references datasets(dataset_id) on delete cascade,
    id text not null,
    title text not null default '',
    is_external boolean not null default false,
    content_text text not null default '',
    text_sha256 text not null default '',
    content_available boolean not null default false,
    raw_html text not null default '',
    raw_html_sha256 text not null default '',
    raw_html_encoding text not null default 'utf-8',
    categories text[] not null default '{}',
    facets jsonb not null default '[]'::jsonb,
    payload jsonb not null,
    primary key (dataset_id, id)
);

create table if not exists legal_units (
    dataset_id text not null references datasets(dataset_id) on delete cascade,
    unit_id text not null,
    document_id text not null,
    parent_unit_id text,
    unit_type text not null,
    ordinal_raw text not null default '',
    label text not null default '',
    heading text not null default '',
    text text not null default '',
    source_start integer,
    source_end integer,
    text_sha256 text not null default '',
    raw_fragment_sha256 text not null default '',
    parse_method text not null,
    parse_confidence double precision not null default 0,
    parser_version text not null,
    payload jsonb not null default '{}'::jsonb,
    primary key (dataset_id, unit_id),
    foreign key (dataset_id, document_id)
        references documents(dataset_id, id) on delete cascade,
    foreign key (dataset_id, parent_unit_id)
        references legal_units(dataset_id, unit_id) on delete cascade
);

create table if not exists document_tables (
    dataset_id text not null references datasets(dataset_id) on delete cascade,
    table_id text not null,
    document_id text not null,
    table_ordinal integer not null,
    source_selector text not null,
    source_fragment_sha256 text not null,
    table_text_sha256 text not null,
    row_count integer not null,
    column_count integer not null,
    extraction_version text not null,
    payload jsonb not null default '{}'::jsonb,
    primary key (dataset_id, table_id),
    foreign key (dataset_id, document_id)
        references documents(dataset_id, id) on delete cascade
);

create table if not exists table_cells (
    dataset_id text not null,
    table_id text not null,
    row_index integer not null,
    column_index integer not null,
    header text not null default '',
    row_header text not null default '',
    value text not null default '',
    cell_tag text not null default 'td',
    colspan integer not null default 1,
    rowspan integer not null default 1,
    payload jsonb not null default '{}'::jsonb,
    primary key (dataset_id, table_id, row_index, column_index),
    foreign key (dataset_id, table_id)
        references document_tables(dataset_id, table_id) on delete cascade
);

create table if not exists chunks (
    dataset_id text not null,
    chunk_id text not null,
    id text not null,
    source_key text not null,
    document_id text not null,
    chunk_order integer not null,
    text text not null default '',
    section_title text not null default '',
    embedding_input_text text not null default '',
    embedding_input_sha256 text not null default '',
    embedding extensions.vector(1536),
    embedding_model text,
    embedding_dimensions integer,
    embedding_preprocessor text,
    embedding_normalized boolean,
    embedded_input_sha256 text,
    embedding_created_at timestamptz,
    search_vector tsvector,
    payload jsonb not null,
    primary key (dataset_id, chunk_id),
    unique (id),
    unique (dataset_id, source_key),
    unique (dataset_id, document_id, chunk_order),
    foreign key (dataset_id, document_id)
        references documents(dataset_id, id) on delete cascade
);

create index if not exists dataset_nodes_title_idx
    on documents (dataset_id, title);
create index if not exists dataset_chunks_document_idx
    on chunks (dataset_id, document_id, chunk_order);
create index if not exists dataset_chunks_search_idx
    on chunks using gin (search_vector);

create or replace view active_document_nodes WITH (security_invoker = true) AS
select n.*, r.fingerprint as dataset_version
from documents n
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where n.dataset_id = runtime.active_dataset_id;

create or replace view active_document_content WITH (security_invoker = true) AS
select d.dataset_id, d.id as document_id, d.content_text, d.text_sha256,
       d.payload, r.fingerprint as dataset_version
from documents d
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where d.dataset_id = runtime.active_dataset_id;

create or replace view active_document_html WITH (security_invoker = true) AS
select d.dataset_id, d.id as document_id, d.raw_html, d.raw_html_sha256,
       d.raw_html_encoding as encoding, d.payload, r.fingerprint as dataset_version
from documents d
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where d.dataset_id = runtime.active_dataset_id;

create or replace view active_document_tables WITH (security_invoker = true) AS
select t.*, r.fingerprint as dataset_version
from document_tables t
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where t.dataset_id = runtime.active_dataset_id;

create or replace view active_table_cells WITH (security_invoker = true) AS
select c.*, r.fingerprint as dataset_version
from table_cells c
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where c.dataset_id = runtime.active_dataset_id;

create or replace view active_document_categories WITH (security_invoker = true) AS
select d.dataset_id, d.id as document_id, category, r.fingerprint as dataset_version
from documents d
cross join lateral unnest(d.categories) as category
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where d.dataset_id = runtime.active_dataset_id;

create or replace view active_legal_units WITH (security_invoker = true) AS
select u.*, r.fingerprint as dataset_version
from legal_units u
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where u.dataset_id = runtime.active_dataset_id;

create or replace view active_graph_chunks WITH (security_invoker = true) AS
select c.*, r.fingerprint as dataset_version
from chunks c
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where c.dataset_id = runtime.active_dataset_id;

-- The corpus is public reference data, so anonymous/authenticated clients may
-- read it through the active views. Writes remain restricted to the database
-- worker/service role because no INSERT/UPDATE/DELETE policies are defined.
do $$
declare
    table_name text;
begin
    foreach table_name in array ARRAY[
        'datasets', 'dataset_state', 'documents',
        'legal_units', 'document_tables', 'table_cells',
        'chunks'
    ] loop
        execute format('alter table public.%I enable row level security', table_name);
        execute format('drop policy if exists public_read on public.%I', table_name);
        execute format(
            'create policy public_read on public.%I for select to anon, authenticated using (true)',
            table_name
        );
    end loop;
end $$;
