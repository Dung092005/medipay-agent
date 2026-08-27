BEGIN;
ALTER TABLE public.table_cell_facts
    DROP CONSTRAINT IF EXISTS table_cell_facts_document_fk;
DROP INDEX IF EXISTS public.table_cell_facts_document_unit_idx;
ALTER TABLE public.table_cell_facts
    DROP COLUMN IF EXISTS document_id,
    DROP COLUMN IF EXISTS legal_unit_id;
COMMIT;
