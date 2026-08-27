-- Firebase identity and release status compatibility fixes.
-- Firebase `sub` is an opaque string, not necessarily a UUID.
BEGIN;

-- Keep Supabase's UUID-compatible auth.uid() untouched on existing installs;
-- policies use this Firebase-specific accessor instead.
CREATE OR REPLACE FUNCTION auth.firebase_uid() RETURNS text
LANGUAGE sql STABLE
AS $$ SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '') $$;

DROP POLICY IF EXISTS authenticated_read_own ON public.users;
CREATE POLICY authenticated_read_own ON public.users
    FOR SELECT TO authenticated USING (uid = auth.firebase_uid());

DROP POLICY IF EXISTS owner_all ON public.conversations;
CREATE POLICY owner_all ON public.conversations
    FOR ALL TO authenticated
    USING (owner_uid = auth.firebase_uid())
    WITH CHECK (owner_uid = auth.firebase_uid());
DROP POLICY IF EXISTS owner_all ON public.conversation_turns;
CREATE POLICY owner_all ON public.conversation_turns
    FOR ALL TO authenticated
    USING (owner_uid = auth.firebase_uid())
    WITH CHECK (owner_uid = auth.firebase_uid());

DROP POLICY IF EXISTS admin_review_read ON public.review_queue_items;
CREATE POLICY admin_review_read ON public.review_queue_items
    FOR SELECT TO authenticated USING (EXISTS (
        SELECT 1 FROM public.users u WHERE u.uid = auth.firebase_uid() AND u.role = 'admin'
    ));
DROP POLICY IF EXISTS admin_review_update ON public.review_queue_items;
CREATE POLICY admin_review_update ON public.review_queue_items
    FOR UPDATE TO authenticated USING (EXISTS (
        SELECT 1 FROM public.users u WHERE u.uid = auth.firebase_uid() AND u.role = 'admin'
    )) WITH CHECK (EXISTS (
        SELECT 1 FROM public.users u WHERE u.uid = auth.firebase_uid() AND u.role = 'admin'
    ));
DROP POLICY IF EXISTS admin_audit_read ON public.review_audit_events;
CREATE POLICY admin_audit_read ON public.review_audit_events
    FOR SELECT TO authenticated USING (EXISTS (
        SELECT 1 FROM public.users u WHERE u.uid = auth.firebase_uid() AND u.role = 'admin'
    ));

ALTER TABLE public.datasets DROP CONSTRAINT IF EXISTS datasets_status_check;
ALTER TABLE public.datasets
    ADD CONSTRAINT datasets_status_check
    CHECK (status IN ('staging', 'active', 'verified', 'failed', 'superseded'));

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'datasets', 'dataset_state', 'documents', 'document_aliases',
        'legal_units', 'document_tables', 'table_cells', 'chunks'
    ] LOOP
        EXECUTE format('DROP POLICY IF EXISTS active_release_read ON public.%I', table_name);
    END LOOP;
    CREATE POLICY active_release_read ON public.datasets
        FOR SELECT TO anon, authenticated USING (
            status IN ('active', 'verified') AND dataset_id =
            (SELECT active_dataset_id FROM public.dataset_state WHERE singleton)
        );
    CREATE POLICY active_release_read ON public.dataset_state
        FOR SELECT TO anon, authenticated USING (singleton);
    FOREACH table_name IN ARRAY ARRAY[
        'documents', 'document_aliases', 'legal_units', 'document_tables',
        'table_cells', 'chunks'
    ] LOOP
        EXECUTE format(
            'CREATE POLICY active_release_read ON public.%I FOR SELECT TO anon, authenticated USING (dataset_id = (SELECT active_dataset_id FROM public.dataset_state WHERE singleton))',
            table_name
        );
    END LOOP;
END $$;

COMMIT;
