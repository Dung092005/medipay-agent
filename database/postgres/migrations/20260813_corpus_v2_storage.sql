-- Add corpus-v2 provenance/eligibility columns without rewriting current rows.
-- Safe on the existing release: constant defaults are metadata-only on PG 17.

begin;

create table if not exists public.document_aliases (
    dataset_id text not null references public.datasets(dataset_id) on delete cascade,
    alias_document_id text not null,
    canonical_document_id text not null,
    alias_type text not null default '',
    confidence text not null default '',
    reason text not null default '',
    evidence_url text not null default '',
    payload jsonb not null default '{}'::jsonb,
    primary key (dataset_id, alias_document_id),
    foreign key (dataset_id, canonical_document_id)
        references public.documents(dataset_id, id) on delete cascade
);

alter table public.legal_units
    add column if not exists source_selector text not null default '',
    add column if not exists source_fragment_sha256 text not null default '';

alter table public.chunks
    add column if not exists unit_id text,
    add column if not exists source_start integer,
    add column if not exists source_end integer,
    add column if not exists text_sha256 text not null default '',
    add column if not exists parser_version text not null default '',
    add column if not exists chunker_version text not null default '',
    add column if not exists lexical_eligible boolean not null default true,
    add column if not exists semantic_eligible boolean not null default true;

alter table public.document_aliases enable row level security;
drop policy if exists public_read on public.document_aliases;
create policy public_read on public.document_aliases
    for select to anon, authenticated using (true);

create or replace view public.active_document_aliases
with (security_invoker = true) as
select a.*, r.fingerprint as dataset_version
from public.document_aliases a
join public.dataset_state runtime on runtime.singleton
join public.datasets r on r.dataset_id = runtime.active_dataset_id
where a.dataset_id = runtime.active_dataset_id;

-- Keep the API response contract while avoiding a second copy of document
-- text/HTML inside JSONB. The boolean is projected from its physical column.
create or replace view public.active_document_content
with (security_invoker = true) as
select d.dataset_id, d.id as document_id, d.content_text, d.text_sha256,
       d.payload || jsonb_build_object(
           'content_available', d.content_available
       ) as payload,
       r.fingerprint as dataset_version
from public.documents d
join public.dataset_state runtime on runtime.singleton
join public.datasets r on r.dataset_id = runtime.active_dataset_id
where d.dataset_id = runtime.active_dataset_id;

grant select on public.document_aliases to anon, authenticated;
grant select on public.active_document_aliases to anon, authenticated;

commit;
