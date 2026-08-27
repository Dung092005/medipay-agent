BEGIN;
DROP TABLE IF EXISTS corpus.chunks_shadow;
DROP TABLE IF EXISTS corpus.legal_units_shadow;
DROP TABLE IF EXISTS corpus.documents_shadow;
DROP TABLE IF EXISTS ops.release_rehearsals;
DROP SCHEMA IF EXISTS app;
DROP SCHEMA IF EXISTS corpus;
DROP SCHEMA IF EXISTS ops;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medipay_app') THEN DROP ROLE medipay_app; END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medipay_corpus') THEN DROP ROLE medipay_corpus; END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medipay_ops') THEN DROP ROLE medipay_ops; END IF;
END
$$;
COMMIT;
