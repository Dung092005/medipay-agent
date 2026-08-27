-- Typed Subject–Attribute–Temporal projection for exact table-value lookup.
-- It is an additive, opt-in index; canonical table_cells remain authoritative.

BEGIN;

CREATE TABLE IF NOT EXISTS public.table_cell_facts (
    fact_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id text NOT NULL REFERENCES public.datasets(dataset_id) ON DELETE CASCADE,
    table_id text NOT NULL,
    row_index integer NOT NULL,
    column_index integer NOT NULL,
    subject text NOT NULL DEFAULT '',
    attribute text NOT NULL DEFAULT '',
    value text NOT NULL DEFAULT '',
    value_normalized text GENERATED ALWAYS AS
      (lower(regexp_replace(trim(value), '\\s+', ' ', 'g'))) STORED,
    effective_from date,
    effective_to date,
    source_selector text NOT NULL DEFAULT '',
    source_fragment_sha256 text NOT NULL DEFAULT '',
    value_sha256 text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (dataset_id, table_id, row_index, column_index),
    FOREIGN KEY (dataset_id, table_id)
        REFERENCES public.document_tables(dataset_id, table_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS table_cell_facts_subject_attribute_idx
    ON public.table_cell_facts(dataset_id, subject, attribute);
CREATE INDEX IF NOT EXISTS table_cell_facts_value_idx
    ON public.table_cell_facts(dataset_id, value_normalized);
CREATE INDEX IF NOT EXISTS table_cell_facts_temporal_idx
    ON public.table_cell_facts(dataset_id, effective_from, effective_to);

ALTER TABLE public.table_cell_facts ENABLE ROW LEVEL SECURITY;

COMMIT;
