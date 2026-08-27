BEGIN;
REVOKE ALL ON public.users FROM authenticated;
REVOKE ALL ON public.conversations, public.conversation_turns FROM authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated;
COMMIT;
