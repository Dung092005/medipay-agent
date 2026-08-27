-- Typed, bounded citation anchors for safe multi-turn reference resolution.
-- Anchors are hints only; every turn still re-retrieves the active release.
BEGIN;

ALTER TABLE public.conversation_turns
    ADD COLUMN IF NOT EXISTS anchors jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.conversation_turns
    DROP CONSTRAINT IF EXISTS conversation_turns_anchors_array;
ALTER TABLE public.conversation_turns
    ADD CONSTRAINT conversation_turns_anchors_array
    CHECK (jsonb_typeof(anchors) = 'array' AND jsonb_array_length(anchors) <= 8);

COMMIT;
