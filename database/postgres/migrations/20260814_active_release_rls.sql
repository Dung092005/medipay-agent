-- Expose only the runtime active corpus to client roles.
-- Worker/service roles retain write access through BYPASSRLS.
begin;

do $$
declare
    table_name text;
begin
    foreach table_name in array ARRAY[
        'datasets', 'dataset_state', 'documents', 'document_aliases',
        'legal_units', 'document_tables', 'table_cells', 'chunks'
    ] loop
        execute format('alter table public.%I enable row level security', table_name);
        execute format('drop policy if exists public_read on public.%I', table_name);
        execute format('drop policy if exists active_release_read on public.%I', table_name);
    end loop;

    create policy active_release_read on public.datasets
        for select to anon, authenticated using (
            status = 'active' and dataset_id =
            (select active_dataset_id from public.dataset_state where singleton)
        );
    create policy active_release_read on public.dataset_state
        for select to anon, authenticated using (singleton);

    foreach table_name in array ARRAY[
        'documents', 'document_aliases', 'legal_units', 'document_tables',
        'table_cells', 'chunks'
    ] loop
        execute format(
            'create policy active_release_read on public.%I for select to anon, authenticated ' ||
            'using (dataset_id = (select active_dataset_id from public.dataset_state where singleton))',
            table_name
        );
    end loop;
end $$;

commit;
