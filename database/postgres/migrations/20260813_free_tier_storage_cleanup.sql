-- Free-tier storage cleanup for the live Supabase database.
--
-- Measured before this migration on 2026-08-13:
--   all databases: 538,098,485 bytes (513 MB as reported by PostgreSQL)
--   public.relationships: 0 rows / 1,646,592 bytes
--
-- The graph contract stores relationships in Neo4j.  The PostgreSQL table and
-- active view are therefore stale.  The remaining DROP INDEX statements only
-- remove byte-for-byte equivalent indexes while retaining one usable index (or
-- the UNIQUE constraint index) for every access path.

BEGIN;

DO $$
DECLARE
    relationship_count bigint;
BEGIN
    IF to_regclass('public.relationships') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM public.relationships'
            INTO relationship_count;
        IF relationship_count <> 0 THEN
            RAISE EXCEPTION
                'Refusing to drop public.relationships: expected 0 rows, found %',
                relationship_count;
        END IF;
    END IF;

    IF to_regclass('public.chunks') IS NOT NULL THEN
        IF to_regclass(
            'public.release_graph_chunks_release_id_source_key_key'
        ) IS NULL THEN
            RAISE EXCEPTION 'Missing retained UNIQUE index for chunks(dataset_id, source_key)';
        END IF;
        IF to_regclass('public.release_graph_chunks_id_key') IS NULL THEN
            RAISE EXCEPTION 'Missing retained UNIQUE index for chunks(id)';
        END IF;
        IF to_regclass(
            'public.release_graph_chunks_release_id_document_id_chunk_order_key'
        ) IS NULL THEN
            RAISE EXCEPTION
                'Missing retained UNIQUE index for chunks(dataset_id, document_id, chunk_order)';
        END IF;
        IF to_regclass('public.dataset_chunks_search_idx') IS NULL THEN
            RAISE EXCEPTION 'Missing retained GIN index for chunks(search_vector)';
        END IF;
    END IF;

    IF to_regclass('public.documents') IS NOT NULL
       AND to_regclass('public.dataset_nodes_title_idx') IS NULL THEN
        RAISE EXCEPTION 'Missing retained index for documents(dataset_id, title)';
    END IF;
END $$;

DROP VIEW IF EXISTS public.active_graph_relationships;
DROP TABLE IF EXISTS public.relationships;

-- UNIQUE constraints already provide these three lookup paths.
DROP INDEX IF EXISTS public.dataset_chunks_source_key_idx;
DROP INDEX IF EXISTS public.release_chunks_source_key_idx;
DROP INDEX IF EXISTS public.dataset_chunks_id_idx;
DROP INDEX IF EXISTS public.release_chunks_id_idx;
DROP INDEX IF EXISTS public.dataset_chunks_document_idx;
DROP INDEX IF EXISTS public.release_chunks_document_idx;

-- Retain dataset_chunks_search_idx and dataset_nodes_title_idx respectively.
DROP INDEX IF EXISTS public.release_chunks_search_idx;
DROP INDEX IF EXISTS public.release_nodes_title_idx;

COMMIT;

ANALYZE public.chunks;
ANALYZE public.documents;
