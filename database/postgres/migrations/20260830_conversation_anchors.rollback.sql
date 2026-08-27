BEGIN;
ALTER TABLE public.conversation_turns
    DROP CONSTRAINT IF EXISTS conversation_turns_anchors_array;
ALTER TABLE public.conversation_turns
    DROP COLUMN IF EXISTS anchors;
COMMIT;
