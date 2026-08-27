BEGIN;
DROP INDEX IF EXISTS public.documents_dataset_signature_normalized_idx;
ALTER TABLE public.documents DROP COLUMN IF EXISTS signature_normalized;
COMMIT;
