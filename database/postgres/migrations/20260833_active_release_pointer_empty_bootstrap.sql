-- Make the guarded activation function create the pointer after an empty
-- bootstrap, where dataset_state has no active release yet.
BEGIN;

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
    IF current_id IS NULL THEN
        INSERT INTO ops.active_release(singleton, active_dataset_id, generation, activated_by)
        VALUES (true, p_dataset_id, 1, left(coalesce(p_actor, 'publisher'), 128))
        ON CONFLICT (singleton) DO UPDATE
        SET previous_dataset_id = NULLIF(ops.active_release.active_dataset_id, p_dataset_id),
            active_dataset_id = EXCLUDED.active_dataset_id,
            generation = ops.active_release.generation + 1,
            activated_at = now(),
            activated_by = EXCLUDED.activated_by;
    ELSE
        UPDATE ops.active_release
        SET previous_dataset_id = NULLIF(current_id, p_dataset_id),
            active_dataset_id = p_dataset_id,
            generation = generation + 1,
            activated_at = now(),
            activated_by = left(coalesce(p_actor, 'publisher'), 128)
        WHERE singleton;
    END IF;
    UPDATE public.dataset_state
    SET active_dataset_id = p_dataset_id
    WHERE singleton;
END;
$$;

REVOKE ALL ON FUNCTION ops.activate_release(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.activate_release(text, text) TO medipay_ops;

COMMIT;
