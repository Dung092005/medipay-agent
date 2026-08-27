-- Use only before any corpus-v2 release has been ingested.
begin;
drop view if exists public.active_document_aliases;
drop table if exists public.document_aliases;
alter table public.chunks
    drop column if exists unit_id,
    drop column if exists source_start,
    drop column if exists source_end,
    drop column if exists text_sha256,
    drop column if exists parser_version,
    drop column if exists chunker_version,
    drop column if exists lexical_eligible,
    drop column if exists semantic_eligible;
alter table public.legal_units
    drop column if exists source_selector,
    drop column if exists source_fragment_sha256;
commit;
