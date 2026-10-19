"""One-shot, private, snapshot-consistent copy; never switches apps or starts jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import make_url, URL
from packages.pms.connection_config import placeholder_password, tls_connect_options, connection_failure_code
from packages.pms.database import online_engine
from packages.pms.recovery import OBJECTS_SQL, archive_sha

TARGET_HOST = "ep-bitter-forest-azwuzrwh.c-3.ap-southeast-1.aws.neon.tech"
ARCHIVE_ENDPOINT = "https://br-twilight-sea-azumjkpc.storage.c-3.ap-southeast-1.aws.neon.tech"
BUCKET = "pms-private-archive"
PUBLIC = {"benchmarks", "corporate_events", "financial_facts", "market_bars",
          "securities", "security_benchmarks", "sources"}
LIMIT = 900 * 1024 * 1024  # reserve capacity below the verified 1 GiB project limit
DEPS = """WITH RECURSIVE edges AS (
 SELECT r.ev_class source_oid,d.refobjid target_oid FROM pg_rewrite r
 JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=r.oid
 AND d.refclassid='pg_class'::regclass
 UNION SELECT conrelid,confrelid FROM pg_constraint WHERE contype='f'
), deps(oid) AS (
 SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='pms'
 UNION SELECT e.target_oid FROM deps d JOIN edges e ON e.source_oid=d.oid
) SELECT c.oid,n.nspname,c.relname,c.relkind,pg_total_relation_size(c.oid) bytes
FROM deps d JOIN pg_class c ON c.oid=d.oid JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','p','v','m','S')
ORDER BY n.nspname,c.relname"""


class CopyBlocked(Exception):
    pass


def require(condition, code):
    if not condition:
        raise CopyBlocked(code)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def qualified(row):
    return quote(row['nspname']) + '.' + quote(row['relname'])


def connection_url(raw, *, destination=False):
    require(bool(raw), 'DESTINATION_URL_MISSING' if destination else 'SOURCE_URL_MISSING')
    try:
        u = make_url(raw)
    except (ArgumentError, ValueError, TypeError):
        # SQLAlchemy URL errors can echo the complete secret; never propagate them.
        raise CopyBlocked('DESTINATION_URL_INVALID' if destination else 'SOURCE_URL_INVALID') from None
    require(u.drivername in {'postgresql', 'postgresql+psycopg', 'postgresql+psycopg2'}, 'POSTGRES_URL_REQUIRED')
    for name, value in [('HOST', u.host), ('USERNAME', u.username), ('PASSWORD', u.password), ('DATABASE', u.database)]:
        require(bool(value), 'DATABASE_' + name + '_MISSING')
    require(not placeholder_password(u.password), 'DATABASE_PASSWORD_PLACEHOLDER')
    require(all(isinstance(v, str) for v in u.query.values()), 'AMBIGUOUS_CONNECTION_OPTIONS')
    # Never guess a Supabase pooler region or change credentials from the saved secret.
    if destination:
        require((u.host, u.database, u.username, u.port or 5432) ==
                (TARGET_HOST, 'pms', 'pms_owner', 5432), 'WRONG_NEON_DESTINATION')
    else:
        require((u.host.startswith('db.cxrkicqggsuylirzohqw.') and u.host.endswith('.supabase.co')) or
                (u.host.endswith('.pooler.supabase.com') and u.username == 'postgres.cxrkicqggsuylirzohqw'),
                'WRONG_SUPABASE_SOURCE')
        require((u.port or 5432) == 5432, 'SESSION_CONNECTION_REQUIRED')
    require(not (set(u.query) - {'sslmode', 'sslrootcert', 'channel_binding', 'connect_timeout', 'application_name'}),
            'UNREVIEWED_CONNECTION_OPTIONS')
    opts = tls_connect_options(u.query, {'PMS_DATABASE_VERIFY_FULL': '1'}, host=u.host)
    return u.update_query_dict({**opts, 'connect_timeout': '15'}).render_as_string(hide_password=False)


def pg_environment(url):
    u = make_url(url)
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    env.update(PGPASSWORD=u.password, PGOPTIONS='-c timezone=UTC -c datestyle=ISO,YMD')
    dsn = URL.create('postgresql', username=u.username, host=u.host, port=u.port,
                     database=u.database, query=u.query).render_as_string(hide_password=False)
    return env, dsn


def command(args, *, env=None, stdout=None, timeout=1800):
    r = subprocess.run(args, env=env, stdout=stdout or subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout, check=False)
    require(r.returncode == 0, 'POSTGRES_TOOL_FAILED')
    return r.stdout


def selected_catalog(conn):
    rows = [dict(r) for r in conn.execute(text(DEPS)).mappings()]
    require({r['relname'] for r in rows if r['nspname'] == 'public'} == PUBLIC,
            'SOURCE_DEPENDENCIES_CHANGED')
    require(all(r['nspname'] in {'pms', 'public'} for r in rows), 'EXTERNAL_SCHEMA_REVIEW_REQUIRED')
    require(all(r['relkind'] != 'm' for r in rows), 'MATERIALIZED_VIEW_REVIEW_REQUIRED')
    require(sum(r['bytes'] for r in rows) < LIMIT, 'SOURCE_CAPACITY_LIMIT')
    ids = [r['oid'] for r in rows]
    # Table-mode pg_dump doesn't include externally defined types/functions/triggers.
    # Block those instead of silently manufacturing their definitions.
    custom = conn.execute(text("""SELECT count(*) FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid
      JOIN pg_namespace n ON n.oid=t.typnamespace WHERE a.attrelid=ANY(:ids) AND a.attnum>0
      AND NOT a.attisdropped AND n.nspname NOT IN ('pg_catalog','pms')"""), {'ids': ids}).scalar_one()
    require(not custom, 'CUSTOM_COLUMN_TYPE_REVIEW_REQUIRED')
    external = conn.execute(text("""SELECT count(*) FROM pg_depend d JOIN pg_proc p ON p.oid=d.refobjid
      JOIN pg_namespace n ON n.oid=p.pronamespace WHERE d.refclassid='pg_proc'::regclass
      AND n.nspname NOT IN ('pg_catalog','pms') AND (
       (d.classid='pg_attrdef'::regclass AND d.objid IN (SELECT oid FROM pg_attrdef WHERE adrelid=ANY(:ids))) OR
       (d.classid='pg_rewrite'::regclass AND d.objid IN (SELECT oid FROM pg_rewrite WHERE ev_class=ANY(:ids))) OR
       (d.classid='pg_constraint'::regclass AND d.objid IN (SELECT oid FROM pg_constraint WHERE conrelid=ANY(:ids)))
      )"""), {'ids': ids}).scalar_one()
    require(not external, 'EXTERNAL_FUNCTION_REVIEW_REQUIRED')
    triggers = conn.execute(text("""SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE t.tgrelid=ANY(:ids)
      AND NOT t.tgisinternal AND n.nspname='public'"""), {'ids': ids}).scalar_one()
    require(not triggers, 'EXTERNAL_TRIGGER_REVIEW_REQUIRED')
    seqs = [dict(r) for r in conn.execute(text("""SELECT DISTINCT s.oid,n.nspname,s.relname,'S' AS relkind,0 AS bytes
      FROM pg_class s JOIN pg_namespace n ON n.oid=s.relnamespace JOIN pg_depend d ON d.objid=s.oid
      AND d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass
      WHERE s.relkind='S' AND d.refobjid=ANY(:ids) AND d.deptype IN ('a','i')
      AND n.nspname='public' ORDER BY n.nspname,s.relname"""), {'ids': ids}).mappings()]
    return rows + [r for r in seqs if r['oid'] not in ids]


def data_manifest(conn, rows):
    result = {}
    conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    conn.execute(text("SET LOCAL DateStyle TO 'ISO, YMD'"))
    for row in rows:
        if row['relkind'] not in {'r', 'p', 'v'}:
            continue
        # Hash sorted row hashes in bounded buckets; ordering and duplicate counts matter.
        buckets = [dict(r) for r in conn.execute(text(f"""WITH h AS
          (SELECT md5(row_to_json(t)::text) h FROM {qualified(row)} t)
          SELECT left(h,2) bucket,count(*) rows,md5(string_agg(h,'' ORDER BY h)) digest
          FROM h GROUP BY left(h,2) ORDER BY bucket""")).mappings()]
        result[row['nspname'] + '.' + row['relname']] = buckets
    return result


def safety(conn):
    row = dict(conn.execute(text('SELECT execution_mode,new_orders_enabled FROM pms.safety_state')).mappings().one())
    require(row == {'execution_mode': 'PAPER', 'new_orders_enabled': False}, 'SOURCE_SAFETY_NOT_LOCKED')
    return row


def sequence_manifest(conn, rows):
    return {r['nspname'] + '.' + r['relname']: dict(conn.execute(text(
        f'SELECT last_value,is_called FROM {qualified(r)}')).mappings().one())
        for r in rows if r['relkind'] == 'S'}


def verify_sequences(actual, baseline):
    require(set(actual) == set(baseline), 'SEQUENCE_SET_MISMATCH')
    for name, before in baseline.items():
        after = actual[name]
        require(after['last_value'] >= before['last_value'] and
                (not before['is_called'] or after['is_called']), 'SEQUENCE_REGRESSION')


def archive_configuration():
    require(bool(os.getenv('PMS_NEON_ARCHIVE_JSON')), 'PRIVATE_ARCHIVE_CREDENTIAL_MISSING')
    try:
        config = json.loads(os.environ['PMS_NEON_ARCHIVE_JSON'])
    except (ValueError, TypeError):
        raise CopyBlocked('ARCHIVE_CREDENTIAL_JSON_INVALID') from None
    require(isinstance(config, dict), 'ARCHIVE_CREDENTIAL_CONFIGURATION')
    require(set(config) == {'access_key_id', 'secret_access_key'} and all(isinstance(v, str) and v.strip() for v in config.values()), 'ARCHIVE_CREDENTIAL_CONFIGURATION')
    return config


def archive_client():
    config = archive_configuration()
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url=ARCHIVE_ENDPOINT, region_name='ap-southeast-1',
                        aws_access_key_id=config['access_key_id'], aws_secret_access_key=config['secret_access_key'],
                        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                                      retries={'max_attempts': 3, 'mode': 'standard'}))


def private_backup(client, files, prefix):
    # Listing is bounded by the provider's 5GB Free allowance; stop below it.
    used = sum(o['Size'] for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
               for o in page.get('Contents', []))
    require(used + sum(p.stat().st_size for p in files) < 4_000_000_000, 'PRIVATE_ARCHIVE_CAPACITY_LIMIT')
    receipts = []
    for path in files:
        key = prefix + '/' + path.name
        digest = archive_sha(path)
        client.upload_file(str(path), BUCKET, key, ExtraArgs={'Metadata': {'sha256': digest}})
        body = client.get_object(Bucket=BUCKET, Key=key)['Body']
        h = hashlib.sha256()
        try:
            for chunk in iter(lambda: body.read(1024 * 1024), b''):
                h.update(chunk)
        finally:
            body.close()
        require(h.hexdigest() == digest, 'PRIVATE_BACKUP_CHECKSUM_MISMATCH')
        receipts.append({'key': key, 'sha256': digest, 'bytes': path.stat().st_size})
    return receipts


def filtered_toc(archive, target):
    lines = command(['pg_restore', '--list', str(archive)]).decode().splitlines()
    # Supabase API principals are intentionally not recreated. Keep RLS enabled,
    # remove policies referring to those roles, and revoke PUBLIC access on restore.
    kept = [line for line in lines if not any(token in line for token in
            (' POLICY ', ' COMMENT ', ' ACL ', ' DEFAULT ACL '))]
    target.write_text('\n'.join(kept) + '\n')


def copy(source, target, src, dst, report):
    report['stage'] = 'archive_configuration'
    client = archive_client()  # fail before any dump/restore when archival is unavailable
    report['stage'] = 'source_snapshot'
    with src.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
        with conn.begin():
            conn.execute(text('SET TRANSACTION READ ONLY'))
            conn.execute(text("SET LOCAL statement_timeout='600000'"))
            conn.execute(text("SET LOCAL idle_in_transaction_session_timeout='1800000'"))
            snapshot = conn.execute(text('SELECT pg_export_snapshot()')).scalar_one()
            rows = selected_catalog(conn)
            expected_safety = safety(conn)
            baseline_sequences = sequence_manifest(conn, rows)
            with tempfile.TemporaryDirectory(prefix='pms-private-copy-') as folder:
                root = Path(folder)
                source_env, source_dsn = pg_environment(source)
                archives = [root/'public-dependencies.dump', root/'pms.dump']
                public_args = [arg for r in rows if r['nspname'] == 'public' for arg in ('--table', qualified(r))]
                for archive, selection in zip(archives, [public_args, ['--schema=pms']]):
                    report['stage'] = 'dump_public' if archive == archives[0] else 'dump_pms'
                    command(['pg_dump', '--format=custom', '--no-owner', '--no-privileges',
                             '--snapshot', snapshot, '--lock-wait-timeout=15000', '--dbname', source_dsn,
                             '--file', str(archive), *selection], env=source_env)
                report['stage'] = 'source_data_manifest'
                expected = data_manifest(conn, rows)
                manifest = root/'manifest.json'
                manifest.write_text(json.dumps({'tables': expected, 'safety': expected_safety,
                                                'sequence_baseline': baseline_sequences}, sort_keys=True))
                report['stage'] = 'private_backup'
                report['private_backups'] = private_backup(client, [*archives, manifest],
                     'database-copies/' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex)
                # All six sections share a single psql transaction. Constraints and
                # triggers are restored after COPY, avoiding mutation of the source audit log.
                report['stage'] = 'render_restore'
                tocs = []
                for i, archive in enumerate(archives):
                    toc = root/f'{i}.list'; filtered_toc(archive, toc); tocs.append(toc)
                sql = root/'restore.sql'
                with sql.open('wb') as stream:
                    for section in ('pre-data', 'data', 'post-data'):
                        for archive, toc in zip(archives, tocs):
                            command(['pg_restore', '--no-owner', '--no-privileges', '--section', section,
                                     '--use-list', str(toc), '--file=-', str(archive)], stdout=stream)
                    stream.write(b'\nREVOKE ALL ON SCHEMA pms, public FROM PUBLIC;\n'
                                 b'REVOKE ALL ON ALL TABLES IN SCHEMA pms, public FROM PUBLIC;\n'
                                 b'REVOKE ALL ON ALL SEQUENCES IN SCHEMA pms, public FROM PUBLIC;\n'
                                 b'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA pms, public FROM PUBLIC;\n')
                with dst.connect() as c:
                    require(c.execute(text(OBJECTS_SQL)).scalar_one() == 0, 'DESTINATION_NOT_EMPTY')
                env, dsn = pg_environment(target)
                report['stage'] = 'destination_restore'
                report['restore_attempted'] = True
                command(['psql', '--no-psqlrc', '--single-transaction', '--set', 'ON_ERROR_STOP=1',
                         '--dbname', dsn, '--file', str(sql)], env=env)
                report['stage'] = 'destination_verification'
                with dst.connect().execution_options(isolation_level='REPEATABLE READ') as c:
                    with c.begin():
                        require(data_manifest(c, rows) == expected, 'RESTORED_DATA_MISMATCH')
                        require(safety(c) == expected_safety, 'RESTORED_SAFETY_MISMATCH')
                        verify_sequences(sequence_manifest(c, rows), baseline_sequences)
                        require(c.execute(text('SELECT pg_database_size(current_database())')).scalar_one() < LIMIT,
                                'DESTINATION_CAPACITY_LIMIT')
                report.update(status='COPIED_DATA_MATCHED', data_copied=True, cutover=False,
                              tables_verified=len(expected), acceptance='NOT_CERTIFIED')


def configuration_checks():
    """Validate each secret independently without connections or secret output."""
    checks = {}
    for name, validate in (
        ('NEON_PMS_DATABASE_URL', lambda: connection_url(os.getenv('NEON_PMS_DATABASE_URL', ''), destination=True)),
        ('PMS_DATABASE_URL', lambda: connection_url(os.getenv('PMS_DATABASE_URL', ''))),
        ('PMS_NEON_ARCHIVE_JSON', archive_configuration),
    ):
        try:
            validate()
            checks[name] = 'FORMAT_VALID'
        except CopyBlocked as exc:
            checks[name] = str(exc)
        except Exception:
            checks[name] = 'CONFIGURATION_INVALID'
    return checks


def run(mode, report):
    report['configuration_checks'] = configuration_checks()
    report['stage'] = 'destination_configuration'
    target = connection_url(os.environ.get('NEON_PMS_DATABASE_URL', ''), destination=True)
    src, dst = None, online_engine(target, worker=True)
    try:
        report['stage'] = 'destination_connection'
        with dst.connect() as c:
            version = int(c.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
            require(version // 10000 == 17, 'POSTGRES_MAJOR_VERSION_MISMATCH')
            require(c.execute(text(OBJECTS_SQL)).scalar_one() == 0, 'DESTINATION_NOT_EMPTY')
            report['destination_connection_verified_full'] = True
        report['stage'] = 'source_configuration'
        source = connection_url(os.environ.get('PMS_DATABASE_URL', ''))
        src = online_engine(source, worker=True)
        report['stage'] = 'source_connection'
        with src.connect() as c:
            version = int(c.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
            require(version // 10000 == 17, 'POSTGRES_MAJOR_VERSION_MISMATCH')
            report['source_connection_verified_full'] = True
        report['stage'] = 'source_dependencies'
        with src.connect().execution_options(isolation_level='REPEATABLE READ') as c:
            with c.begin():
                c.execute(text('SET TRANSACTION READ ONLY'))
                rows = selected_catalog(c)
                safety(c)
                report['source_dependency_bytes'] = sum(r['bytes'] for r in rows)
        report['private_archive_credential_present'] = bool(os.getenv('PMS_NEON_ARCHIVE_JSON'))
        report['status'] = 'CONNECTIONS_VERIFIED_COPY_PENDING'
        report['stage'] = 'check_complete'
        if mode == 'copy':
            report['stage'] = 'private_backup_and_copy'
            copy(source, target, src, dst, report)
            report['stage'] = 'copy_complete'
        return report
    finally:
        if src is not None:
            src.dispose()
        dst.dispose()


def failure_code(exc):
    """Allowlisted codes only: SDK/driver messages can contain secret values."""
    if isinstance(exc, CopyBlocked):
        return str(exc)
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        code = response.get('Error', {}).get('Code')
        storage_codes = {'AccessDenied', 'InvalidAccessKeyId', 'SignatureDoesNotMatch',
                         'NoSuchBucket', 'InvalidToken', 'ExpiredToken'}
        if code in storage_codes:
            return 'ARCHIVE_' + code.upper()
    cause = getattr(exc, 'orig', exc)
    state = getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None)
    if isinstance(state, str) and len(state) == 5 and state.isascii() and state.isalnum():
        return 'DATABASE_SQLSTATE_' + state
    if isinstance(exc, subprocess.TimeoutExpired):
        return 'POSTGRES_TOOL_TIMEOUT'
    return connection_failure_code(exc)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['check', 'copy'], default='check')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    require(not args.output.exists(), 'REPORT_ALREADY_EXISTS')
    report = {'status': 'CHECKING', 'data_copied': False, 'cutover': False, 'campaign_started': False}
    try:
        run(args.mode, report)
        code = 0
    except Exception as exc:
        report.update(status='BLOCKED', code=failure_code(exc), data_copied='NOT_VERIFIED')
        code = 2
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
