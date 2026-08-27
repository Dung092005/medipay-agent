-- BHYT / viện phí canonical document store for PostgreSQL.
-- Apply once in Supabase SQL Editor.
-- Semantic vectors are an immutable Qdrant projection; PostgreSQL keeps only
-- embedding input/provenance metadata and generated lexical search.

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

create table if not exists release_projections (
    dataset_id text not null references datasets(dataset_id) on delete cascade,
    projection_kind text not null check (projection_kind in ('postgres', 'qdrant', 'neo4j')),
    locator text not null,
    status text not null check (status in ('building', 'ready', 'failed', 'retired')),
    release_fingerprint text not null,
    expected_count bigint not null default 0 check (expected_count >= 0),
    actual_count bigint check (actual_count is null or actual_count >= 0),
    content_sha256 text not null default '',
    embedding_model text not null default '',
    embedding_dimensions integer check (embedding_dimensions is null or embedding_dimensions > 0),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    verified_at timestamptz,
    primary key (dataset_id, projection_kind),
    unique (projection_kind, locator),
    constraint release_projection_fingerprint_not_blank check (length(trim(release_fingerprint)) > 0)
);
create index if not exists release_projections_status_idx
    on release_projections (status, projection_kind);

create schema if not exists ops;
create table if not exists ops.active_release (
    singleton boolean primary key default true check (singleton),
    active_dataset_id text not null references datasets(dataset_id),
    previous_dataset_id text references datasets(dataset_id),
    generation bigint not null default 1 check (generation > 0),
    activated_at timestamptz not null default now(),
    activated_by text not null default 'bootstrap'
);
insert into ops.active_release (singleton, active_dataset_id, activated_by)
select true, state.active_dataset_id, 'bootstrap'
from dataset_state state
where state.singleton = true and state.active_dataset_id is not null
on conflict (singleton) do nothing;

-- One-shot migration runner ledger; API replicas never write this table.
create table if not exists public.schema_migrations (
    version text primary key,
    checksum text not null,
    applied_at timestamptz not null default now()
);

-- Firebase identity/profile records are kept in PostgreSQL for backend role
-- checks.  The Firebase token remains the authentication authority; this table
-- is deliberately small and contains no patient or conversation payload.
create table if not exists public.users (
    uid text primary key,
    email text not null default '',
    display_name text not null default '',
    photo_url text not null default '',
    role text not null default 'user' check (role in ('admin', 'user')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists users_email_idx on public.users (email);
create index if not exists users_role_idx on public.users (role);

create or replace function public.update_users_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;
drop trigger if exists users_updated_at on public.users;
create trigger users_updated_at
before update on public.users
for each row execute function public.update_users_updated_at();

-- Owner-scoped conversation audit trail. Runtime answers always re-retrieve the
-- active release; these rows are memory/audit state, never evidence authority.
create table if not exists public.conversations (
    conversation_id uuid primary key,
    owner_uid text not null references public.users(uid) on delete cascade,
    title text not null default '',
    active_dataset_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    constraint conversations_title_length check (char_length(title) <= 240)
);

create table if not exists public.conversation_turns (
    turn_id uuid primary key,
    conversation_id uuid not null references public.conversations(conversation_id) on delete cascade,
    owner_uid text not null references public.users(uid) on delete cascade,
    turn_index integer not null check (turn_index > 0),
    user_message text not null check (char_length(user_message) between 1 and 5000),
    assistant_response text not null check (char_length(assistant_response) <= 20000),
    dataset_id text,
    citations jsonb not null default '[]'::jsonb,
    claims jsonb not null default '[]'::jsonb,
    anchors jsonb not null default '[]'::jsonb
        check (jsonb_typeof(anchors) = 'array' and jsonb_array_length(anchors) <= 8),
    request_id text not null default '',
    created_at timestamptz not null default now(),
    unique (conversation_id, turn_index),
    unique (conversation_id, turn_id)
);
create index if not exists conversations_owner_updated_idx
    on public.conversations(owner_uid, updated_at desc) where deleted_at is null;
create index if not exists conversation_turns_owner_created_idx
    on public.conversation_turns(owner_uid, conversation_id, created_at desc);

create table if not exists public.review_queue_items (
    review_id text primary key,
    domain text not null check (domain in ('legal_document', 'hospital_fee_ocr')),
    source_id text not null default '',
    title text not null default '',
    status text not null check (status in ('pending', 'accepted', 'rejected')),
    confidence numeric(5, 4) not null default 0 check (confidence >= 0 and confidence <= 1),
    summary text not null default '',
    payload jsonb not null default '{}'::jsonb,
    submitted_by text not null default '',
    assigned_to text not null default '',
    decision_note text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    decided_at timestamptz
);
create index if not exists review_queue_status_created_idx
    on public.review_queue_items(status, created_at desc);
create table if not exists public.review_audit_events (
    event_id text primary key,
    review_id text not null references public.review_queue_items(review_id) on delete cascade,
    action text not null check (action in ('submitted', 'accepted', 'rejected')),
    actor_uid text not null default '',
    note text not null default '',
    created_at timestamptz not null default now()
);
create index if not exists review_audit_review_created_idx
    on public.review_audit_events(review_id, created_at);
create or replace function public.update_review_queue_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;
drop trigger if exists review_queue_updated_at on public.review_queue_items;
create trigger review_queue_updated_at before update on public.review_queue_items
for each row execute function public.update_review_queue_updated_at();

create or replace function public.update_conversations_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;
drop trigger if exists conversations_updated_at on public.conversations;
create trigger conversations_updated_at before update on public.conversations
for each row execute function public.update_conversations_updated_at();

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
    signature_normalized text generated always as (
        regexp_replace(
            upper(coalesce(payload -> 'metadata' ->> 'so_ky_hieu', payload ->> 'so_ky_hieu', '')),
            '[^A-Z0-9Đ]', '', 'g'
        )
    ) stored,
    primary key (dataset_id, id)
);
create index if not exists documents_dataset_signature_normalized_idx
    on documents(dataset_id, signature_normalized);

-- Identity aliases are citation-resolution data, not knowledge-graph edges.
-- They stay with the authority documents so an old source ID resolves without
-- creating a second searchable copy of the same legal instrument.
create table if not exists document_aliases (
    dataset_id text not null references datasets(dataset_id) on delete cascade,
    alias_document_id text not null,
    canonical_document_id text not null,
    alias_type text not null default '',
    confidence text not null default '',
    reason text not null default '',
    evidence_url text not null default '',
    payload jsonb not null default '{}'::jsonb,
    primary key (dataset_id, alias_document_id),
    foreign key (dataset_id, canonical_document_id)
        references documents(dataset_id, id) on delete cascade
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
    source_selector text not null default '',
    source_fragment_sha256 text not null default '',
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

create table if not exists table_cell_facts (
    fact_id bigint generated always as identity primary key,
    dataset_id text not null references datasets(dataset_id) on delete cascade,
    table_id text not null,
    document_id text not null default '',
    legal_unit_id text not null default '',
    row_index integer not null,
    column_index integer not null,
    subject text not null default '',
    attribute text not null default '',
    value text not null default '',
    value_normalized text generated always as
      (lower(regexp_replace(trim(value), '\\s+', ' ', 'g'))) stored,
    effective_from date,
    effective_to date,
    source_selector text not null default '',
    source_fragment_sha256 text not null default '',
    value_sha256 text not null default '',
    payload jsonb not null default '{}'::jsonb,
    unique(dataset_id, table_id, row_index, column_index),
    foreign key(dataset_id, table_id) references document_tables(dataset_id, table_id) on delete cascade,
    foreign key(dataset_id, document_id) references documents(dataset_id, id) on delete cascade
);
create index if not exists table_cell_facts_subject_attribute_idx
    on table_cell_facts(dataset_id, subject, attribute);
create index if not exists table_cell_facts_value_idx
    on table_cell_facts(dataset_id, value_normalized);
create index if not exists table_cell_facts_document_unit_idx
    on table_cell_facts(dataset_id, document_id, legal_unit_id);

create table if not exists chunks (
    dataset_id text not null,
    chunk_id text not null,
    id text not null,
    source_key text not null,
    document_id text not null,
    chunk_order integer not null,
    unit_id text not null,
    source_start integer not null,
    source_end integer not null,
    text text not null default '',
    section_title text not null default '',
    text_sha256 text not null default '',
    parser_version text not null default '',
    chunker_version text not null default '',
    lexical_eligible boolean not null default true,
    semantic_eligible boolean not null default true,
    embedding_input_text text not null default '',
    embedding_input_sha256 text not null default '',
    embedding_model text,
    embedding_dimensions integer,
    embedding_preprocessor text,
    embedding_normalized boolean,
    embedded_input_sha256 text,
    embedding_created_at timestamptz,
    search_vector tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', coalesce(section_title, '') || ' ' || coalesce(text, ''))) STORED,
    payload jsonb not null,
    primary key (dataset_id, chunk_id),
    unique (id),
    unique (dataset_id, source_key),
    unique (dataset_id, document_id, chunk_order),
    foreign key (dataset_id, document_id)
        references documents(dataset_id, id) on delete cascade,
    foreign key (dataset_id, unit_id)
        references legal_units(dataset_id, unit_id) on delete cascade
);

create index if not exists dataset_nodes_title_idx
    on documents (dataset_id, title);
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
       d.payload || jsonb_build_object(
           'content_available', d.content_available
       ) as payload,
       r.fingerprint as dataset_version
from documents d
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where d.dataset_id = runtime.active_dataset_id;

create or replace view active_document_aliases WITH (security_invoker = true) AS
select a.*, r.fingerprint as dataset_version
from document_aliases a
join dataset_state runtime on runtime.singleton
join datasets r on r.dataset_id = runtime.active_dataset_id
where a.dataset_id = runtime.active_dataset_id;

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

-- The corpus is public reference data, but only the active release is exposed
-- to client roles.  Staging/superseded releases remain worker-only; this is
-- important because the views below use security_invoker.
do $$
declare
    table_name text;
begin
    foreach table_name in array ARRAY[
        'datasets', 'dataset_state', 'documents', 'document_aliases',
        'release_projections',
        'legal_units', 'document_tables', 'table_cells',
        'chunks', 'users', 'conversations', 'conversation_turns',
        'review_queue_items', 'review_audit_events'
    ] loop
        execute format('alter table public.%I enable row level security', table_name);
        execute format('drop policy if exists public_read on public.%I', table_name);
        execute format('drop policy if exists active_release_read on public.%I', table_name);
    end loop;

    create policy active_release_read on public.datasets
        for select to anon, authenticated
        using (status = 'active' and dataset_id =
               (select active_dataset_id from public.dataset_state where singleton));
    create policy active_release_read on public.dataset_state
        for select to anon, authenticated using (singleton);

    foreach table_name in array ARRAY[
        'documents', 'document_aliases', 'release_projections', 'legal_units', 'document_tables',
        'table_cells', 'chunks'
    ] loop
        execute format(
            'create policy active_release_read on public.%I for select to anon, authenticated ' ||
            'using (dataset_id = (select active_dataset_id from public.dataset_state where singleton))',
            table_name
        );
    end loop;

    drop policy if exists service_role_all on public.users;
    drop policy if exists authenticated_read_own on public.users;
    create policy service_role_all on public.users
        for all to service_role using (true) with check (true);
    create policy authenticated_read_own on public.users
        for select to authenticated using (uid = auth.uid()::text);

    drop policy if exists service_role_all on public.conversations;
    drop policy if exists service_role_all on public.conversation_turns;
    drop policy if exists owner_all on public.conversations;
    drop policy if exists owner_all on public.conversation_turns;
    create policy service_role_all on public.conversations
        for all to service_role using (true) with check (true);
    create policy service_role_all on public.conversation_turns
        for all to service_role using (true) with check (true);
    create policy owner_all on public.conversations
        for all to authenticated using (owner_uid = auth.uid()::text)
        with check (owner_uid = auth.uid()::text);
    create policy owner_all on public.conversation_turns
        for all to authenticated using (owner_uid = auth.uid()::text)
        with check (owner_uid = auth.uid()::text);
    drop policy if exists admin_review_read on public.review_queue_items;
    drop policy if exists admin_review_update on public.review_queue_items;
    drop policy if exists admin_audit_read on public.review_audit_events;
    create policy admin_review_read on public.review_queue_items
        for select to authenticated using (exists (
            select 1 from public.users u where u.uid = auth.uid()::text and u.role = 'admin'
        ));
    create policy admin_review_update on public.review_queue_items
        for update to authenticated using (exists (
            select 1 from public.users u where u.uid = auth.uid()::text and u.role = 'admin'
        )) with check (exists (
            select 1 from public.users u where u.uid = auth.uid()::text and u.role = 'admin'
        ));
    create policy admin_audit_read on public.review_audit_events
        for select to authenticated using (exists (
            select 1 from public.users u where u.uid = auth.uid()::text and u.role = 'admin'
        ));
end $$;
