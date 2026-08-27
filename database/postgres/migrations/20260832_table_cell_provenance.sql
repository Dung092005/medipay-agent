-- Complete the table-cell ontology anchor: every fact carries its canonical
-- document and an optional source legal-unit key when extraction supplied it.
BEGIN;

ALTER TABLE public.table_cell_facts
    ADD COLUMN IF NOT EXISTS document_id text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS legal_unit_id text NOT NULL DEFAULT '';

UPDATE public.table_cell_facts f
SET document_id = t.document_id,
    legal_unit_id = COALESCE(NULLIF(f.payload ->> 'unit_id', ''), '')
FROM public.document_tables t
WHERE t.dataset_id = f.dataset_id AND t.table_id = f.table_id;

ALTER TABLE public.table_cell_facts
    DROP CONSTRAINT IF EXISTS table_cell_facts_document_fk;
ALTER TABLE public.table_cell_facts
    ADD CONSTRAINT table_cell_facts_document_fk
    FOREIGN KEY (dataset_id, document_id)
    REFERENCES public.documents(dataset_id, id)
    ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS table_cell_facts_document_unit_idx
    ON public.table_cell_facts(dataset_id, document_id, legal_unit_id);

COMMIT;
