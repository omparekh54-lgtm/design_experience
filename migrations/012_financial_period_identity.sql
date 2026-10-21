-- Preserve quarter and annual observations from the same exchange disclosure.
-- Run transactionally before deploying FINANCIAL_XBRL_v2 writers. No source rows
-- are deleted or reclassified. Supports the isolated pms local-table/view layout
-- and the original standalone table layout.
DO $$
DECLARE
    target regclass;
    ns text;
    tab text;
    old_index record;
BEGIN
    target := coalesce(to_regclass('pms.financial_filings_local'),
                       to_regclass('pms.financial_filings'),
                       to_regclass('financial_filings'));
    IF target IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_class WHERE oid=target AND relkind IN ('r','p')
    ) THEN
        RAISE EXCEPTION 'financial_period_migration_requires_writable_table';
    END IF;
    SELECT n.nspname,c.relname INTO ns,tab FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=target;
    EXECUTE format('LOCK TABLE %s IN SHARE ROW EXCLUSIVE MODE', target);
    -- Remove only the previous standalone identity index on this exact table.
    -- Other constraints and unrelated schemas (including public source data)
    -- remain untouched.
    FOR old_index IN
        SELECT ni.nspname,index_class.relname
        FROM pg_index ix JOIN pg_class index_class ON index_class.oid=ix.indexrelid
        JOIN pg_namespace ni ON ni.oid=index_class.relnamespace
        WHERE ix.indrelid=target AND ix.indisunique AND NOT ix.indisprimary
          AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conindid=ix.indexrelid)
          AND (SELECT array_agg(a.attname::text ORDER BY k.ordinality)
               FROM unnest(ix.indkey) WITH ORDINALITY k(attnum,ordinality)
               JOIN pg_attribute a ON a.attrelid=target AND a.attnum=k.attnum)
              = ARRAY['instrument_id','period_end','filing_timestamp','source','consolidated','revision_number']
    LOOP
        EXECUTE format('DROP INDEX %I.%I', old_index.nspname, old_index.relname);
    END LOOP;
    EXECUTE format('CREATE UNIQUE INDEX IF NOT EXISTS %I ON %s
        (instrument_id,period_end,filing_timestamp,source,consolidated,revision_number,filing_kind)',
        tab || '_period_identity_uq', target);
END $$;
