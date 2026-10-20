-- Correct the duplicate introduced by the first cloud master pass only.
-- Preserve the original instrument ID and external backfill mapping.
DO $review$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pms.instruments WHERE id=307 AND nse_symbol='HEG'
   AND isin='INE545A01024' AND external_security_id IS NOT NULL AND active)
 OR NOT EXISTS(SELECT 1 FROM pms.instruments WHERE id=2419 AND nse_symbol='HEGAM'
   AND isin='INE545A01024' AND external_security_id IS NULL AND active
   AND metadata ? 'cloud_master_observed_at')
 OR EXISTS(SELECT 1 FROM pms.cloud_daily_prices n JOIN pms.cloud_daily_prices o
   ON o.instrument_id=307 AND n.trade_date=o.trade_date AND n.source=o.source
   WHERE n.instrument_id=2419)
 THEN RAISE EXCEPTION 'reviewed_duplicate_identity_changed'; END IF;
 UPDATE pms.instruments SET active=false,trading_symbol='HEGAM-INGESTION-DUPLICATE-2419',
   metadata=metadata || jsonb_build_object('ingestion_duplicate_of',307,
     'reviewed_on','2026-10-20','original_trading_symbol','HEGAM-EQ') WHERE id=2419;
 UPDATE pms.instruments SET nse_symbol='HEGAM',trading_symbol='HEGAM-EQ',
   metadata=metadata || jsonb_build_object('reviewed_symbol_history',jsonb_build_array(
     jsonb_build_object('previous_symbol','HEG','current_symbol','HEGAM',
     'isin','INE545A01024','observed_on','2026-10-20',
     'master_sha256','64bc3997cc8e6d67f77fec2d04e4f74c45d7f7cb712ffd55c0885fb5a56722d8',
     'dated_price_sha256','b3aba7f1e20587e4c77a5de2b720cbcd5d8f4c8fe42e1b67f06f0d7deaabbf46')))
   WHERE id=307;
 UPDATE pms.cloud_daily_prices SET instrument_id=307 WHERE instrument_id=2419;
END
$review$;
