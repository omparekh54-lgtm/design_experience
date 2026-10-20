-- Expansion of migration010 for the reviewed Neon copy's view-backed layout.
-- Previous pms.daily_prices view MD5 with search_path=pms,public:
-- b89187eb1a43cc6b8bce631c3ef36741
-- Apply as one transaction to the isolated Neon PMS database only.
CREATE TABLE IF NOT EXISTS pms.cloud_daily_prices (
  instrument_id bigint NOT NULL REFERENCES pms.instruments(id),
  trade_date date NOT NULL,
  open numeric NOT NULL,
  high numeric NOT NULL,
  low numeric NOT NULL,
  close numeric NOT NULL,
  volume numeric NOT NULL,
  turnover numeric NOT NULL,
  source text NOT NULL DEFAULT 'NSE_UDIFF',
  ingested_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(instrument_id,trade_date,source),
  CHECK(open>0 AND low>0 AND close>0 AND high>=greatest(open,close,low)
    AND low<=least(open,close) AND volume>=0 AND turnover>=0),
  CHECK(open::text NOT IN ('NaN','Infinity','-Infinity')
    AND high::text NOT IN ('NaN','Infinity','-Infinity')
    AND low::text NOT IN ('NaN','Infinity','-Infinity')
    AND close::text NOT IN ('NaN','Infinity','-Infinity')
    AND volume::text NOT IN ('NaN','Infinity','-Infinity')
    AND turnover::text NOT IN ('NaN','Infinity','-Infinity'))
);
CREATE OR REPLACE VIEW pms.daily_prices AS
SELECT i.id AS instrument_id,(mb.ts AT TIME ZONE 'Asia/Kolkata')::date AS trade_date,
       mb.open,mb.high,mb.low,mb.close,mb.volume,
       CASE WHEN mb.close IS NOT NULL AND mb.volume IS NOT NULL THEN mb.close*mb.volume
            ELSE NULL::numeric END AS turnover,
       mb.provider AS source,mb.ts AS ingested_at
FROM public.market_bars mb JOIN pms.instruments i ON i.external_security_id=mb.security_id
WHERE mb."interval"='1d'
UNION ALL
SELECT instrument_id,trade_date,open,high,low,close,volume,turnover,source,ingested_at
FROM pms.cloud_daily_prices;
REVOKE ALL ON pms.cloud_daily_prices FROM PUBLIC;
ALTER TABLE pms.cloud_daily_prices ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS provider_snapshots_record_queue_idx
  ON pms.provider_snapshots(provider,dataset,(metadata->>'record_key'));
