BEGIN;
ALTER TABLE public.documents
    ADD COLUMN IF NOT EXISTS signature_normalized text GENERATED ALWAYS AS (
        regexp_replace(
            upper(coalesce(payload -> 'metadata' ->> 'so_ky_hieu', payload ->> 'so_ky_hieu', '')),
            '[^A-Z0-9Đ]', '', 'g'
        )
    ) STORED;
CREATE INDEX IF NOT EXISTS documents_dataset_signature_normalized_idx
    ON public.documents(dataset_id, signature_normalized);
COMMIT;
