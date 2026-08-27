BEGIN;
DROP TABLE IF EXISTS public.review_audit_events;
DROP TABLE IF EXISTS public.review_queue_items;
DROP FUNCTION IF EXISTS public.update_review_queue_updated_at();
COMMIT;
