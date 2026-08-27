BEGIN;
REVOKE ALL ON FUNCTION ops.activate_release(text, text) FROM PUBLIC;
DROP FUNCTION IF EXISTS ops.activate_release(text, text);
DROP TABLE IF EXISTS ops.active_release;
COMMIT;
