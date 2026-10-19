-- Isolated Neon PMS copy: test on a branch before applying to main.
-- Replace only the derived ID ordering. Stored source and local rows are unchanged.
-- Reviewed previous view definition MD5 with search_path=pms,public:
-- 014fe357ead50a515be18e1c04464d44
CREATE OR REPLACE VIEW pms.shares_outstanding_history AS
SELECT l.id,l.instrument_id,l.effective_from,l.effective_to,l.shares_outstanding,
       l.source,l.knowable_at,l.raw_payload
FROM pms.shares_outstanding_history_local l
UNION ALL
SELECT 1000000000000::bigint + row_number() OVER
         (ORDER BY f.security_id,f.period_end,f.source_id,f.id) AS id,
       i.id AS instrument_id,f.period_end AS effective_from,NULL::date AS effective_to,
       f.value AS shares_outstanding,
       upper(COALESCE(s.metadata->>'exchange',s.metadata->>'provider',s.source_type)) AS source,
       COALESCE(s.published_at,s.retrieved_at,f.created_at) AS knowable_at,
       jsonb_build_object('india_ai_source_id',f.source_id::text,
                          'source_fact',f.fact_name,'source_data',f.data) AS raw_payload
FROM public.financial_facts f
JOIN pms.instruments i ON i.external_security_id=f.security_id
LEFT JOIN public.sources s ON s.id=f.source_id
WHERE f.fact_name='shares_outstanding' AND f.value>0 AND f.period_end IS NOT NULL;
