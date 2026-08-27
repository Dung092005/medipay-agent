-- Shadow contract for the expand/dual-read rehearsal.
-- The public release tables remain the source of truth until parity and a
-- rollback window are signed off.  Shadow rows use internal bigint keys while
-- retaining immutable external IDs for citation and cutover checks.

BEGIN;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS corpus;
CREATE SCHEMA IF NOT EXISTS app;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medipay_ops') THEN
        CREATE ROLE medipay_ops NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medipay_corpus') THEN
        CREATE ROLE medipay_corpus NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medipay_app') THEN
        CREATE ROLE medipay_app NOLOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS ops.release_rehearsals (
    rehearsal_id text PRIMARY KEY,
    dataset_id text NOT NULL,
    source_schema text NOT NULL DEFAULT 'public',
    shadow_schema text NOT NULL DEFAULT 'corpus',
    status text NOT NULL CHECK (status IN ('building', 'parity', 'cutover', 'rolled_back', 'failed')),
    source_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    shadow_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    mismatches jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    verified_at timestamptz
);

CREATE TABLE IF NOT EXISTS corpus.documents_shadow (
    internal_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id text NOT NULL,
    source_document_id text NOT NULL,
    title text NOT NULL DEFAULT '',
    text_sha256 text NOT NULL DEFAULT '',
    categories text[] NOT NULL DEFAULT '{}',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (dataset_id, source_document_id)
);

CREATE TABLE IF NOT EXISTS corpus.legal_units_shadow (
    internal_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id text NOT NULL,
    source_unit_id text NOT NULL,
    document_internal_id bigint NOT NULL REFERENCES corpus.documents_shadow(internal_id) ON DELETE CASCADE,
    parent_internal_id bigint REFERENCES corpus.legal_units_shadow(internal_id) ON DELETE CASCADE,
    unit_type text NOT NULL DEFAULT '',
    label text NOT NULL DEFAULT '',
    heading text NOT NULL DEFAULT '',
    text text NOT NULL DEFAULT '',
    text_sha256 text NOT NULL DEFAULT '',
    source_start integer,
    source_end integer,
    UNIQUE (dataset_id, source_unit_id)
);

CREATE TABLE IF NOT EXISTS corpus.chunks_shadow (
    internal_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id text NOT NULL,
    source_chunk_id text NOT NULL,
    document_internal_id bigint NOT NULL REFERENCES corpus.documents_shadow(internal_id) ON DELETE CASCADE,
    unit_internal_id bigint REFERENCES corpus.legal_units_shadow(internal_id) ON DELETE SET NULL,
    source_key text NOT NULL DEFAULT '',
    chunk_order integer NOT NULL DEFAULT 0,
    section_title text NOT NULL DEFAULT '',
    text text NOT NULL DEFAULT '',
    text_sha256 text NOT NULL DEFAULT '',
    embedding_input_sha256 text NOT NULL DEFAULT '',
    lexical_eligible boolean NOT NULL DEFAULT true,
    semantic_eligible boolean NOT NULL DEFAULT true,
    UNIQUE (dataset_id, source_chunk_id)
);

CREATE INDEX IF NOT EXISTS documents_shadow_dataset_title_idx
    ON corpus.documents_shadow(dataset_id, title);
CREATE INDEX IF NOT EXISTS chunks_shadow_dataset_document_idx
    ON corpus.chunks_shadow(dataset_id, document_internal_id, chunk_order);
CREATE INDEX IF NOT EXISTS legal_units_shadow_dataset_document_idx
    ON corpus.legal_units_shadow(dataset_id, document_internal_id);

ALTER TABLE ops.release_rehearsals ENABLE ROW LEVEL SECURITY;
ALTER TABLE corpus.documents_shadow ENABLE ROW LEVEL SECURITY;
ALTER TABLE corpus.legal_units_shadow ENABLE ROW LEVEL SECURITY;
ALTER TABLE corpus.chunks_shadow ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA app, corpus TO medipay_app;
GRANT USAGE ON SCHEMA corpus TO medipay_corpus;
GRANT USAGE ON SCHEMA ops TO medipay_ops;
GRANT SELECT ON corpus.documents_shadow, corpus.legal_units_shadow, corpus.chunks_shadow TO medipay_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON corpus.documents_shadow, corpus.legal_units_shadow, corpus.chunks_shadow TO medipay_corpus;
GRANT SELECT, INSERT, UPDATE ON ops.release_rehearsals TO medipay_ops;

COMMIT;
