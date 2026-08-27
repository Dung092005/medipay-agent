begin;
do $$
declare
    table_name text;
begin
    foreach table_name in array ARRAY[
        'datasets', 'dataset_state', 'documents', 'document_aliases',
        'legal_units', 'document_tables', 'table_cells', 'chunks'
    ] loop
        execute format('drop policy if exists active_release_read on public.%I', table_name);
    end loop;
    foreach table_name in array ARRAY[
        'datasets', 'dataset_state', 'documents', 'document_aliases',
        'legal_units', 'document_tables', 'table_cells', 'chunks'
    ] loop
        execute format(
            'create policy public_read on public.%I for select to anon, authenticated using (true)',
            table_name
        );
    end loop;
end $$;
commit;
