BEGIN;
-- Keep the previous guarded function from 20260831 on rollback; operators
-- must review the pointer function before reverting an active release.
COMMIT;
