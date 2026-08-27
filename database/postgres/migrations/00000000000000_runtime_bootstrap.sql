-- Fresh disposable/local runtime bootstrap.
-- Managed Supabase installations should baseline this file after a reviewed
-- inventory; it is intentionally independent of pgvector because production
-- semantic vectors live in Qdrant.

CREATE SCHEMA IF NOT EXISTS public;
CREATE SCHEMA IF NOT EXISTS auth;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN BYPASSRLS;
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
LANGUAGE sql STABLE
AS $$ SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid $$;

CREATE TABLE IF NOT EXISTS public.datasets (
    dataset_id text PRIMARY KEY,
    fingerprint text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('staging', 'active', 'failed', 'superseded')),
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    collection_name text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    failure_reason text
);

CREATE TABLE IF NOT EXISTS public.dataset_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_dataset_id text REFERENCES public.datasets(dataset_id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.dataset_state(singleton) VALUES (true) ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.documents (
    dataset_id text NOT NULL REFERENCES public.datasets(dataset_id) ON DELETE CASCADE,
    id text NOT NULL,
    title text NOT NULL DEFAULT '',
    is_external boolean NOT NULL DEFAULT false,
    content_text text NOT NULL DEFAULT '',
    text_sha256 text NOT NULL DEFAULT '',
    content_available boolean NOT NULL DEFAULT false,
    raw_html text NOT NULL DEFAULT '',
    raw_html_sha256 text NOT NULL DEFAULT '',
    raw_html_encoding text NOT NULL DEFAULT 'utf-8',
    categories text[] NOT NULL DEFAULT '{}',
    facets jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(dataset_id, id)
);

CREATE TABLE IF NOT EXISTS public.document_aliases (
    dataset_id text NOT NULL REFERENCES public.datasets(dataset_id) ON DELETE CASCADE,
    alias_document_id text NOT NULL,
    canonical_document_id text NOT NULL,
    alias_type text NOT NULL DEFAULT '',
    confidence text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    evidence_url text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(dataset_id, alias_document_id),
    FOREIGN KEY(dataset_id, canonical_document_id)
        REFERENCES public.documents(dataset_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.legal_units (
    dataset_id text NOT NULL REFERENCES public.datasets(dataset_id) ON DELETE CASCADE,
    unit_id text NOT NULL,
    document_id text NOT NULL,
    parent_unit_id text,
    unit_type text NOT NULL DEFAULT '',
    ordinal_raw text NOT NULL DEFAULT '',
    label text NOT NULL DEFAULT '',
    heading text NOT NULL DEFAULT '',
    text text NOT NULL DEFAULT '',
    source_start integer,
    source_end integer,
    source_selector text NOT NULL DEFAULT '',
    source_fragment_sha256 text NOT NULL DEFAULT '',
    text_sha256 text NOT NULL DEFAULT '',
    raw_fragment_sha256 text NOT NULL DEFAULT '',
    parse_method text NOT NULL DEFAULT '',
    parse_confidence double precision NOT NULL DEFAULT 0,
    parser_version text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(dataset_id, unit_id),
    FOREIGN KEY(dataset_id, document_id)
        REFERENCES public.documents(dataset_id, id) ON DELETE CASCADE,
    FOREIGN KEY(dataset_id, parent_unit_id)
        REFERENCES public.legal_units(dataset_id, unit_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.document_tables (
    dataset_id text NOT NULL REFERENCES public.datasets(dataset_id) ON DELETE CASCADE,
    table_id text NOT NULL,
    document_id text NOT NULL,
    table_ordinal integer NOT NULL DEFAULT 0,
    source_selector text NOT NULL DEFAULT '',
    source_fragment_sha256 text NOT NULL DEFAULT '',
    table_text_sha256 text NOT NULL DEFAULT '',
    row_count integer NOT NULL DEFAULT 0,
    column_count integer NOT NULL DEFAULT 0,
    extraction_version text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(dataset_id, table_id),
    FOREIGN KEY(dataset_id, document_id)
        REFERENCES public.documents(dataset_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.table_cells (
    dataset_id text NOT NULL,
    table_id text NOT NULL,
    row_index integer NOT NULL,
    column_index integer NOT NULL,
    header text NOT NULL DEFAULT '',
    row_header text NOT NULL DEFAULT '',
    value text NOT NULL DEFAULT '',
    cell_tag text NOT NULL DEFAULT 'td',
    colspan integer NOT NULL DEFAULT 1,
    rowspan integer NOT NULL DEFAULT 1,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(dataset_id, table_id, row_index, column_index),
    FOREIGN KEY(dataset_id, table_id)
        REFERENCES public.document_tables(dataset_id, table_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.chunks (
    dataset_id text NOT NULL,
    chunk_id text NOT NULL,
    id text NOT NULL,
    source_key text NOT NULL,
    document_id text NOT NULL,
    chunk_order integer NOT NULL DEFAULT 0,
    unit_id text NOT NULL DEFAULT '',
    source_start integer,
    source_end integer,
    text text NOT NULL DEFAULT '',
    section_title text NOT NULL DEFAULT '',
    text_sha256 text NOT NULL DEFAULT '',
    parser_version text NOT NULL DEFAULT '',
    chunker_version text NOT NULL DEFAULT '',
    lexical_eligible boolean NOT NULL DEFAULT true,
    semantic_eligible boolean NOT NULL DEFAULT true,
    embedding_input_text text NOT NULL DEFAULT '',
    embedding_input_sha256 text NOT NULL DEFAULT '',
    embedding_model text,
    embedding_dimensions integer,
    embedding_preprocessor text,
    embedding_normalized boolean,
    embedded_input_sha256 text,
    embedding_created_at timestamptz,
    search_vector tsvector GENERATED ALWAYS AS
      (to_tsvector('simple', coalesce(section_title, '') || ' ' || coalesce(text, ''))) STORED,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(dataset_id, chunk_id),
    UNIQUE(id),
    UNIQUE(dataset_id, source_key),
    UNIQUE(dataset_id, document_id, chunk_order),
    FOREIGN KEY(dataset_id, document_id)
        REFERENCES public.documents(dataset_id, id) ON DELETE CASCADE,
    FOREIGN KEY(dataset_id, unit_id)
        REFERENCES public.legal_units(dataset_id, unit_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS dataset_nodes_title_idx ON public.documents(dataset_id, title);
CREATE INDEX IF NOT EXISTS dataset_chunks_search_idx ON public.chunks USING gin(search_vector);
CREATE UNIQUE INDEX IF NOT EXISTS release_graph_chunks_release_id_source_key_key
    ON public.chunks(dataset_id, source_key);
CREATE UNIQUE INDEX IF NOT EXISTS release_graph_chunks_id_key ON public.chunks(id);
CREATE UNIQUE INDEX IF NOT EXISTS release_graph_chunks_release_id_document_id_chunk_order_key
    ON public.chunks(dataset_id, document_id, chunk_order);

CREATE OR REPLACE VIEW public.active_document_nodes AS
SELECT d.*, r.fingerprint AS dataset_version
FROM public.documents d
JOIN public.dataset_state s ON s.singleton
JOIN public.datasets r ON r.dataset_id = s.active_dataset_id
WHERE d.dataset_id = s.active_dataset_id;

ALTER TABLE public.datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dataset_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.legal_units ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_tables ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.table_cells ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chunks ENABLE ROW LEVEL SECURITY;
