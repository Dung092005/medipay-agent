BEGIN;
-- Explicit grants make the RLS contract testable on a fresh PostgreSQL target.
-- Policies remain the owner boundary; grants alone do not authorize another UID.
GRANT SELECT ON public.users TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.conversations, public.conversation_turns TO authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
COMMIT;
