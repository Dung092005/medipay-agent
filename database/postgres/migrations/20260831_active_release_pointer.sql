-- Single control-plane pointer for release cutover/rollback bookkeeping.
-- The legacy public.dataset_state row is retained during the shadow window;
-- runtime reads this pointer first once it exists.
BEGIN;

CREATE TABLE IF NOT EXISTS ops.active_release (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_dataset_id text NOT NULL REFERENCES public.datasets(dataset_id),
    previous_dataset_id text REFERENCES public.datasets(dataset_id),
    generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    activated_at timestamptz NOT NULL DEFAULT now(),
    activated_by text NOT NULL DEFAULT 'migration'
);

INSERT INTO ops.active_release (singleton, active_dataset_id, generation, activated_by)
SELECT true, state.active_dataset_id, 1, 'migration'
FROM public.dataset_state state
WHERE state.singleton = true AND state.active_dataset_id IS NOT NULL
ON CONFLICT (singleton) DO NOTHING;

CREATE OR REPLACE FUNCTION ops.activate_release(p_dataset_id text, p_actor text DEFAULT 'publisher')
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, ops
AS $$
DECLARE
    current_id text;
    ready_count integer;
    fingerprint text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('medipay:active-release'));
    SELECT d.fingerprint INTO fingerprint
    FROM public.datasets d
    WHERE d.dataset_id = p_dataset_id
      AND d.status IN ('active', 'verified');
    IF fingerprint IS NULL THEN
        RAISE EXCEPTION 'release is not publishable: %', p_dataset_id;
    END IF;
    SELECT count(*) INTO ready_count
    FROM public.release_projections p
    WHERE p.dataset_id = p_dataset_id
      AND p.status = 'ready'
      AND p.release_fingerprint = fingerprint
      AND p.actual_count = p.expected_count;
    IF ready_count <> 3 THEN
        RAISE EXCEPTION 'release projection parity failed: %', p_dataset_id;
    END IF;
    SELECT active_dataset_id INTO current_id FROM ops.active_release WHERE singleton;
    UPDATE ops.active_release
    SET previous_dataset_id = NULLIF(current_id, p_dataset_id),
        active_dataset_id = p_dataset_id,
        generation = generation + 1,
        activated_at = now(),
        activated_by = left(coalesce(p_actor, 'publisher'), 128)
    WHERE singleton;
    UPDATE public.dataset_state
    SET active_dataset_id = p_dataset_id
    WHERE singleton;
END;
$$;

REVOKE ALL ON FUNCTION ops.activate_release(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.activate_release(text, text) TO medipay_ops;
GRANT SELECT ON ops.active_release TO medipay_app, medipay_ops;

COMMIT;
