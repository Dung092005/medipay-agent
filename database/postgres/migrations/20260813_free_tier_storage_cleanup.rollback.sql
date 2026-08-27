-- Structural rollback for 20260813_free_tier_storage_cleanup.sql.
-- public.relationships was empty at backup time, so there are no rows to
-- restore.  Authoritative edge data remains in CSV/JSON and Neo4j.

BEGIN;

CREATE TABLE IF NOT EXISTS public.relationships (
    dataset_id text NOT NULL,
    edge_key text NOT NULL,
    source_id text NOT NULL,
    target_id text NOT NULL,
    relationship_type text NOT NULL DEFAULT '',
    payload jsonb NOT NULL,
    CONSTRAINT release_graph_relationships_pkey
        PRIMARY KEY (dataset_id, edge_key),
    CONSTRAINT release_graph_relationships_release_id_source_id_fkey
        FOREIGN KEY (dataset_id, source_id)
        REFERENCES public.documents(dataset_id, id) ON DELETE CASCADE,
    CONSTRAINT release_graph_relationships_release_id_target_id_fkey
        FOREIGN KEY (dataset_id, target_id)
        REFERENCES public.documents(dataset_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS dataset_rel_source_idx
    ON public.relationships(dataset_id, source_id);
CREATE INDEX IF NOT EXISTS dataset_rel_target_idx
    ON public.relationships(dataset_id, target_id);
CREATE INDEX IF NOT EXISTS release_rel_source_idx
    ON public.relationships(dataset_id, source_id);
CREATE INDEX IF NOT EXISTS release_rel_target_idx
    ON public.relationships(dataset_id, target_id);

ALTER TABLE public.relationships ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read ON public.relationships;
CREATE POLICY public_read ON public.relationships
    FOR SELECT TO anon, authenticated USING (true);
GRANT ALL ON TABLE public.relationships TO anon, authenticated, service_role;

CREATE OR REPLACE VIEW public.active_graph_relationships
WITH (security_invoker = true) AS
SELECT e.dataset_id,
       e.edge_key,
       e.source_id,
       e.target_id,
       e.relationship_type,
       e.payload,
       r.fingerprint AS dataset_version
FROM public.relationships e
JOIN public.dataset_state runtime ON runtime.singleton
JOIN public.datasets r ON r.dataset_id = runtime.active_dataset_id
WHERE e.dataset_id = runtime.active_dataset_id;

CREATE UNIQUE INDEX IF NOT EXISTS dataset_chunks_source_key_idx
    ON public.chunks(dataset_id, source_key);
CREATE UNIQUE INDEX IF NOT EXISTS release_chunks_source_key_idx
    ON public.chunks(dataset_id, source_key);
CREATE UNIQUE INDEX IF NOT EXISTS dataset_chunks_id_idx
    ON public.chunks(id);
CREATE UNIQUE INDEX IF NOT EXISTS release_chunks_id_idx
    ON public.chunks(id);
CREATE INDEX IF NOT EXISTS dataset_chunks_document_idx
    ON public.chunks(dataset_id, document_id, chunk_order);
CREATE INDEX IF NOT EXISTS release_chunks_document_idx
    ON public.chunks(dataset_id, document_id, chunk_order);
CREATE INDEX IF NOT EXISTS release_chunks_search_idx
    ON public.chunks USING gin(search_vector);
CREATE INDEX IF NOT EXISTS release_nodes_title_idx
    ON public.documents(dataset_id, title);

COMMIT;
