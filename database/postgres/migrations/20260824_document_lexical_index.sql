BEGIN;

-- Document-level recall is a first-stage operation. Keeping its search
-- vector on the immutable document row avoids scanning every chunk while a
-- request is still deciding which documents deserve dense reranking.
ALTER TABLE public.documents
    ADD COLUMN IF NOT EXISTS document_search_vector tsvector GENERATED ALWAYS AS
      (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content_text, ''))) STORED;

CREATE INDEX IF NOT EXISTS dataset_documents_lexical_idx
    ON public.documents USING gin (document_search_vector);

COMMIT;
