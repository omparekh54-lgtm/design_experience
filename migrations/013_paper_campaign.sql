-- Additive campaign identity store. Applying this migration never activates PAPER.
CREATE TABLE IF NOT EXISTS pms.paper_campaigns (
 id uuid PRIMARY KEY,
 activated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 closed_at timestamptz,
 manifest jsonb NOT NULL,
 CHECK (manifest->>'version'='PAPER_CAMPAIGN_v1'),
 CHECK (manifest->>'status'='ACTIVE'),
 CHECK ((manifest->>'campaign_activated')::boolean IS TRUE),
 CHECK (closed_at IS NULL OR closed_at>=activated_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_campaign_one_active ON pms.paper_campaigns ((true)) WHERE closed_at IS NULL;
CREATE OR REPLACE FUNCTION pms.preserve_campaign_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'campaign_identity_is_immutable'; END IF;
 IF NEW.id<>OLD.id OR NEW.activated_at<>OLD.activated_at OR NEW.manifest<>OLD.manifest
    OR (OLD.closed_at IS NOT NULL AND NEW.closed_at IS DISTINCT FROM OLD.closed_at) THEN
  RAISE EXCEPTION 'campaign_identity_is_immutable';
 END IF;
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='preserve_campaign_identity' AND tgrelid='pms.paper_campaigns'::regclass) THEN
  CREATE TRIGGER preserve_campaign_identity BEFORE UPDATE OR DELETE ON pms.paper_campaigns
  FOR EACH ROW EXECUTE FUNCTION pms.preserve_campaign_identity();
 END IF;
END $$;
REVOKE ALL ON FUNCTION pms.preserve_campaign_identity() FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='pms_status') THEN
  GRANT SELECT ON pms.paper_campaigns TO pms_status;
 END IF;
END $$;
