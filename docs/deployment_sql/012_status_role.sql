-- Read-only role for the existing public status API, with no owner membership.
-- A generated password is provisioned privately after branch verification.
CREATE ROLE pms_status LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE pms TO pms_status;
GRANT USAGE ON SCHEMA pms,public TO pms_status;
GRANT SELECT ON ALL TABLES IN SCHEMA pms,public TO pms_status;
