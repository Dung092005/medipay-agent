-- Owner-scoped conversation persistence. Evidence is copied as an audit
-- snapshot; every answer still re-retrieves the active release.
CREATE TABLE IF NOT EXISTS public.conversations (
    conversation_id uuid PRIMARY KEY,
    owner_uid text NOT NULL REFERENCES public.users(uid) ON DELETE CASCADE,
    title text NOT NULL DEFAULT '',
    active_dataset_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    CONSTRAINT conversations_title_length CHECK (char_length(title) <= 240)
);

CREATE TABLE IF NOT EXISTS public.conversation_turns (
    turn_id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES public.conversations(conversation_id) ON DELETE CASCADE,
    owner_uid text NOT NULL REFERENCES public.users(uid) ON DELETE CASCADE,
    turn_index integer NOT NULL CHECK (turn_index > 0),
    user_message text NOT NULL CHECK (char_length(user_message) BETWEEN 1 AND 5000),
    assistant_response text NOT NULL CHECK (char_length(assistant_response) <= 20000),
    dataset_id text,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    claims jsonb NOT NULL DEFAULT '[]'::jsonb,
    request_id text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(conversation_id, turn_index),
    UNIQUE(conversation_id, turn_id)
);

CREATE INDEX IF NOT EXISTS conversations_owner_updated_idx
    ON public.conversations(owner_uid, updated_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS conversation_turns_owner_created_idx
    ON public.conversation_turns(owner_uid, conversation_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.update_conversations_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS conversations_updated_at ON public.conversations;
CREATE TRIGGER conversations_updated_at
BEFORE UPDATE ON public.conversations
FOR EACH ROW EXECUTE FUNCTION public.update_conversations_updated_at();

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversation_turns ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS service_role_all ON public.conversations;
DROP POLICY IF EXISTS service_role_all ON public.conversation_turns;
DROP POLICY IF EXISTS owner_all ON public.conversations;
DROP POLICY IF EXISTS owner_all ON public.conversation_turns;
CREATE POLICY service_role_all ON public.conversations
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY service_role_all ON public.conversation_turns
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY owner_all ON public.conversations
    FOR ALL TO authenticated
    USING (owner_uid = auth.uid()::text)
    WITH CHECK (owner_uid = auth.uid()::text);
CREATE POLICY owner_all ON public.conversation_turns
    FOR ALL TO authenticated
    USING (owner_uid = auth.uid()::text)
    WITH CHECK (owner_uid = auth.uid()::text);
