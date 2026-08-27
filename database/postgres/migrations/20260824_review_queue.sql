-- Persistent admin review queue. Review payload is derived metadata only;
-- canonical corpus publication still happens through the release pipeline.

BEGIN;

CREATE TABLE IF NOT EXISTS public.review_queue_items (
    review_id text PRIMARY KEY,
    domain text NOT NULL CHECK (domain IN ('legal_document', 'hospital_fee_ocr')),
    source_id text NOT NULL DEFAULT '',
    title text NOT NULL DEFAULT '',
    status text NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
    confidence numeric(5, 4) NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
    summary text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    submitted_by text NOT NULL DEFAULT '',
    assigned_to text NOT NULL DEFAULT '',
    decision_note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz
);

CREATE INDEX IF NOT EXISTS review_queue_status_created_idx
    ON public.review_queue_items (status, created_at DESC);
CREATE INDEX IF NOT EXISTS review_queue_domain_status_idx
    ON public.review_queue_items (domain, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.review_audit_events (
    event_id text PRIMARY KEY,
    review_id text NOT NULL REFERENCES public.review_queue_items(review_id) ON DELETE CASCADE,
    action text NOT NULL CHECK (action IN ('submitted', 'accepted', 'rejected')),
    actor_uid text NOT NULL DEFAULT '',
    note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS review_audit_review_created_idx
    ON public.review_audit_events (review_id, created_at);

CREATE OR REPLACE FUNCTION public.update_review_queue_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS review_queue_updated_at ON public.review_queue_items;
CREATE TRIGGER review_queue_updated_at
BEFORE UPDATE ON public.review_queue_items
FOR EACH ROW EXECUTE FUNCTION public.update_review_queue_updated_at();

ALTER TABLE public.review_queue_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.review_audit_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_review_read ON public.review_queue_items;
DROP POLICY IF EXISTS admin_review_update ON public.review_queue_items;
DROP POLICY IF EXISTS admin_audit_read ON public.review_audit_events;
CREATE POLICY admin_review_read ON public.review_queue_items
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.users u
        WHERE u.uid = auth.uid()::text AND u.role = 'admin'
    ));
CREATE POLICY admin_review_update ON public.review_queue_items
    FOR UPDATE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.users u
        WHERE u.uid = auth.uid()::text AND u.role = 'admin'
    ))
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.users u
        WHERE u.uid = auth.uid()::text AND u.role = 'admin'
    ));
CREATE POLICY admin_audit_read ON public.review_audit_events
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.users u
        WHERE u.uid = auth.uid()::text AND u.role = 'admin'
    ));

GRANT SELECT, UPDATE ON public.review_queue_items TO authenticated;
GRANT SELECT ON public.review_audit_events TO authenticated;

COMMIT;
