-- Hide legacy overlay duplicates only after an exact V2 local replacement exists.
-- Original source/map rows and original source facts are preserved physically.
SET LOCAL search_path=pg_catalog;
DO $$
DECLARE definition text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid=to_regclass('pms.financial_filings') AND relkind='v') THEN RETURN; END IF;
 IF obj_description('pms.financial_filings'::regclass,'pg_class')='PMS_FINANCIAL_V2_OVERLAY_v1' THEN RETURN; END IF;
 IF to_regclass('pms.financial_filings_local') IS NULL THEN RAISE EXCEPTION 'financial_overlay_local_table_required'; END IF;
 definition:=pg_get_viewdef('pms.financial_filings'::regclass,true);
 definition:=regexp_replace(definition,';[[:space:]]*$','');
 EXECUTE 'CREATE OR REPLACE VIEW pms.financial_filings AS SELECT legacy.* FROM (' || definition || ') legacy
 WHERE NOT EXISTS (SELECT 1 FROM pms.financial_filings_local v
 WHERE v.id<>legacy.id AND v.instrument_id=legacy.instrument_id AND v.period_end=legacy.period_end
 AND v.filing_timestamp=legacy.filing_timestamp AND v.source=legacy.source
 AND v.consolidated=legacy.consolidated AND v.revision_number=legacy.revision_number
 AND v.filing_kind=legacy.filing_kind
 AND v.raw_payload->''financial_extraction''->>''version''=''FINANCIAL_XBRL_v2'')';
 definition:=pg_get_viewdef('pms.financial_facts'::regclass,true);
 definition:=regexp_replace(definition,';[[:space:]]*$','');
 -- Filter by visible filing identity, never fall back to an old fact if V2 made it null.
 EXECUTE 'CREATE OR REPLACE VIEW pms.financial_facts AS SELECT legacy.* FROM (' || definition || ') legacy
 WHERE EXISTS(SELECT 1 FROM pms.financial_filings f WHERE f.id=legacy.filing_id)';
 COMMENT ON VIEW pms.financial_filings IS 'PMS_FINANCIAL_V2_OVERLAY_v1';
END $$;
