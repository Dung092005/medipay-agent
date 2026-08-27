-- Release control-plane metadata for every external projection.
-- PostgreSQL remains the canonical source; Qdrant and Neo4j rows describe
-- immutable, independently verified projections of the same release.

BEGIN;

CREATE TABLE IF NOT EXISTS public.release_projections (
    dataset_id text NOT NULL REFERENCES public.datasets(dataset_id) ON DELETE CASCADE,
    projection_kind text NOT NULL CHECK (projection_kind IN ('postgres', 'qdrant', 'neo4j')),
    locator text NOT NULL,
    status text NOT NULL CHECK (status IN ('building', 'ready', 'failed', 'retired')),
    release_fingerprint text NOT NULL,
    expected_count bigint NOT NULL DEFAULT 0 CHECK (expected_count >= 0),
    actual_count bigint CHECK (actual_count IS NULL OR actual_count >= 0),
    content_sha256 text NOT NULL DEFAULT '',
    embedding_model text NOT NULL DEFAULT '',
    embedding_dimensions integer CHECK (embedding_dimensions IS NULL OR embedding_dimensions > 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    verified_at timestamptz,
    PRIMARY KEY (dataset_id, projection_kind),
    UNIQUE (projection_kind, locator),
    CONSTRAINT release_projection_fingerprint_not_blank CHECK (length(trim(release_fingerprint)) > 0)
);

CREATE INDEX IF NOT EXISTS release_projections_status_idx
    ON public.release_projections (status, projection_kind);

ALTER TABLE public.release_projections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS active_release_read ON public.release_projections;
CREATE POLICY active_release_read ON public.release_projections
    FOR SELECT TO anon, authenticated
    USING (dataset_id = (SELECT active_dataset_id FROM public.dataset_state WHERE singleton));

GRANT SELECT ON public.release_projections TO anon, authenticated;

-- Backfill the active release's three control-plane entries.  Existing
-- installations may update actual_count/metadata after a real parity check;
-- this migration never changes the active pointer or source rows.
INSERT INTO public.release_projections (
    dataset_id, projection_kind, locator, status, release_fingerprint,
    expected_count, actual_count, embedding_model, embedding_dimensions,
    metadata, verified_at
)
SELECT
    d.dataset_id,
    'postgres',
    'postgres:public',
    'ready',
    d.fingerprint,
    COALESCE((d.manifest -> 'counts' ->> 'passages')::bigint, 0),
    COALESCE((d.manifest -> 'counts' ->> 'passages')::bigint, 0),
    '', NULL,
    jsonb_build_object('source', 'migration_backfill'),
    now()
FROM public.datasets d
WHERE d.status = 'active'
ON CONFLICT (dataset_id, projection_kind) DO NOTHING;

INSERT INTO public.release_projections (
    dataset_id, projection_kind, locator, status, release_fingerprint,
    expected_count, actual_count, embedding_model, embedding_dimensions,
    metadata, verified_at
)
SELECT
    d.dataset_id,
    'qdrant',
    COALESCE(NULLIF(d.collection_name, ''), 'qdrant:unknown'),
    'ready',
    d.fingerprint,
    COALESCE((d.manifest -> 'counts' ->> 'qdrant_point_count')::bigint,
             (d.manifest -> 'counts' ->> 'semantic_passages')::bigint, 0),
    COALESCE((d.manifest -> 'counts' ->> 'qdrant_point_count')::bigint,
             (d.manifest -> 'counts' ->> 'semantic_passages')::bigint, 0),
    COALESCE(d.manifest ->> 'embedding_model', ''),
    NULLIF(d.manifest ->> 'embedding_dimensions', '')::integer,
    jsonb_build_object('source', 'migration_backfill'),
    now()
FROM public.datasets d
WHERE d.status = 'active'
ON CONFLICT (dataset_id, projection_kind) DO NOTHING;

INSERT INTO public.release_projections (
    dataset_id, projection_kind, locator, status, release_fingerprint,
    expected_count, actual_count, metadata, verified_at
)
SELECT
    d.dataset_id,
    'neo4j',
    'neo4j:' || COALESCE(NULLIF(current_setting('app.neo4j_database', true), ''), 'neo4j'),
    'ready',
    d.fingerprint,
    COALESCE((d.manifest -> 'counts' ->> 'documents')::bigint, 0),
    COALESCE((d.manifest -> 'counts' ->> 'documents')::bigint, 0),
    jsonb_build_object('source', 'migration_backfill'),
    now()
FROM public.datasets d
WHERE d.status = 'active'
ON CONFLICT (dataset_id, projection_kind) DO NOTHING;

COMMIT;
