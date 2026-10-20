"""Archive a current snapshot, download it again, and restore only to local empty PG17."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from packages.pms.database import online_engine
from packages.pms.recovery import OBJECTS_SQL, archive_sha
from packages.data_layer.private_archive import PrivateArchive
from scripts.copy_pms_to_neon import (BUCKET, LIMIT, require, connection_url,
    selected_catalog, data_manifest, sequence_manifest, verify_sequences,
    safety, pg_environment, command, filtered_toc, private_backup, CopyBlocked)

SCHEMA_SQL="""SELECT n.nspname,c.relname,a.attnum,a.attname,
 format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,
 pg_get_expr(d.adbin,d.adrelid) AS default_expression
 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE n.nspname IN ('pms','public') AND c.relkind IN ('r','p','v')
 AND a.attnum>0 AND NOT a.attisdropped ORDER BY n.nspname,c.relname,a.attnum"""
CONSTRAINT_SQL="""SELECT n.nspname,c.relname,k.conname,k.convalidated,
 pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k
 JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname IN ('pms','public') ORDER BY n.nspname,c.relname,k.conname"""


def schema_manifest(c):
    c.execute(text('SET LOCAL search_path=pg_catalog'))
    return {name:[dict(x) for x in c.execute(text(sql)).mappings()]
            for name,sql in [('columns',SCHEMA_SQL),('constraints',CONSTRAINT_SQL),
                ('indexes',"SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname IN ('pms','public') ORDER BY schemaname,tablename,indexname"),
                ('functions',"SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,pg_get_functiondef(p.oid) definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='pms' ORDER BY n.nspname,p.proname,args"),
                ('triggers',"SELECT n.nspname,c.relname,t.tgname,pg_get_triggerdef(t.oid) definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('pms','public') AND NOT t.tgisinternal ORDER BY n.nspname,c.relname,t.tgname"),
                ('rls',"SELECT n.nspname,c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('pms','public') AND c.relkind IN ('r','p') ORDER BY n.nspname,c.relname")]}


def local_target(raw):
    u=make_url(raw)
    require(u.host in {'127.0.0.1','localhost'} and u.database=='pms_recovery'
            and (u.port or 5432)==5432,'RESTORE_TARGET_NOT_ISOLATED_LOCAL')
    require(u.username=='postgres' and bool(u.password),'RESTORE_TEST_ROLE_REQUIRED')
    return u


def download_verified(client,key,path,sha,size):
    require(0<size<200*1024*1024,'BACKUP_DOWNLOAD_LIMIT')
    stream=client.get_object(Bucket=BUCKET,Key=key)['Body']
    h=hashlib.sha256();total=0
    try:
        with path.open('xb') as out:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                total+=len(chunk)
                require(total<=size,'BACKUP_DOWNLOAD_SIZE_MISMATCH')
                out.write(chunk);h.update(chunk)
    finally:
        stream.close()
    require(total==size and h.hexdigest()==sha,'BACKUP_DOWNLOAD_CHECKSUM_MISMATCH')


def rehearse(source,target,archive,root):
    began=time.monotonic()
    stamp=datetime.now(timezone.utc)
    prefix='recovery-backups/'+stamp.strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex
    src=online_engine(source,worker=True)
    dst=create_engine(local_target(target))
    try:
        with dst.connect() as c:
            require(c.execute(text(OBJECTS_SQL)).scalar_one()==0,'RESTORE_TARGET_NOT_EMPTY')
        dump=root/'database.dump';manifest_path=root/'manifest.json'
        # The exported snapshot covers both the dump and the comparison manifest.
        # This uses no advisory collection lock and writes nothing to Neon.
        with src.connect().execution_options(isolation_level='REPEATABLE READ') as c,c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            c.execute(text("SET LOCAL statement_timeout='600000'"))
            c.execute(text("SET LOCAL idle_in_transaction_session_timeout='1800000'"))
            rows=selected_catalog(c)
            require(safety(c)=={'execution_mode':'PAPER','new_orders_enabled':False},'SAFETY_NOT_LOCKED')
            require(c.execute(text('SELECT pg_database_size(current_database())')).scalar_one()<LIMIT,'DATABASE_CAPACITY_RESERVE')
            snapshot=c.execute(text('SELECT pg_export_snapshot()')).scalar_one()
            baseline_sequences=sequence_manifest(c,rows)
            env,dsn=pg_environment(source)
            command(['pg_dump','--format=custom','--no-owner','--no-privileges',
                     '--schema=pms','--schema=public','--snapshot',snapshot,
                     '--lock-wait-timeout=15000','--dbname',dsn,'--file',str(dump)],env=env)
            manifest={'snapshot_at':stamp.isoformat(),'tables':data_manifest(c,rows),
                      'sequences':baseline_sequences,'schema':schema_manifest(c)}
            manifest_path.write_text(json.dumps(manifest,sort_keys=True,default=str))
        # Upload and GET checksum verification precede the isolated restore.
        receipts=private_backup(archive.client,[dump,manifest_path],prefix)
        restored=root/'downloaded.dump'
        download_verified(archive.client,receipts[0]['key'],restored,
                          receipts[0]['sha256'],receipts[0]['bytes'])
        checked_manifest=root/'downloaded-manifest.json'
        download_verified(archive.client,receipts[1]['key'],checked_manifest,
                          receipts[1]['sha256'],receipts[1]['bytes'])
        expected=json.loads(checked_manifest.read_text())
        toc=root/'restore.list';filtered_toc(restored,toc)
        # initdb already owns the empty public schema. Never drop it or use
        # --clean; omit only its CREATE entry, retaining every contained object.
        toc.write_text('\n'.join(line for line in toc.read_text().splitlines()
                                 if ' SCHEMA - public ' not in line)+'\n')
        env,dsn=pg_environment(target)
        command(['pg_restore','--exit-on-error','--single-transaction','--no-owner',
                 '--no-privileges','--use-list',str(toc),'--dbname',dsn,str(restored)],env=env)
        with dst.connect().execution_options(isolation_level='REPEATABLE READ') as c,c.begin():
            require(data_manifest(c,rows)==expected['tables'],'RESTORED_DATA_MISMATCH')
            require(schema_manifest(c)==expected['schema'],'RESTORED_SCHEMA_MISMATCH')
            verify_sequences(sequence_manifest(c,rows),expected['sequences'])
            safety(c)
        # Rebuild only the intended status role in the isolated server. No login
        # password is restored or copied from production.
        with dst.begin() as c:
            c.execute(text('REVOKE ALL ON SCHEMA pms,public FROM PUBLIC'))
            c.execute(text('REVOKE ALL ON ALL TABLES IN SCHEMA pms,public FROM PUBLIC'))
            c.execute(text('REVOKE ALL ON ALL SEQUENCES IN SCHEMA pms,public FROM PUBLIC'))
            c.execute(text('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA pms,public FROM PUBLIC'))
            c.execute(text('CREATE ROLE pms_recovery_status NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'))
            c.execute(text('GRANT USAGE ON SCHEMA pms,public TO pms_recovery_status'))
            c.execute(text('GRANT SELECT ON ALL TABLES IN SCHEMA pms,public TO pms_recovery_status'))
            c.execute(text('SET LOCAL ROLE pms_recovery_status'))
            require(c.execute(text('SELECT count(*) FROM pms.instruments')).scalar_one()>0,'RESTORED_STATUS_READ_FAILED')
            c.execute(text('SAVEPOINT mutation_check'))
            try:
                c.execute(text('UPDATE pms.safety_state SET new_orders_enabled=true WHERE false'))
            except Exception:
                c.execute(text('ROLLBACK TO SAVEPOINT mutation_check'))
            else:
                raise CopyBlocked('RESTORED_STATUS_WRITE_ALLOWED')
        seconds=round(time.monotonic()-began,2)
        require(seconds<=3600,'RESTORE_TIME_OBJECTIVE_EXCEEDED')
        report={'status':'RESTORE_VERIFIED','snapshot_at':stamp.isoformat(),
                'verified_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':seconds,
                'relations_verified':len(expected['tables']),
                'sequences_verified':len(expected['sequences']),
                'schema_and_constraints_matched':True,'role_read_verified':True,
                'role_write_denied':True,'safety_locked':True,
                'archive_sha256':receipts[0]['sha256'],'archive_bytes':receipts[0]['bytes'],
                'manifest_sha256':receipts[1]['sha256'],'private_backup_prefix':prefix,
                'source_writes':False,'production_restore':False,'paper_activated':False,
                'rpo_policy_hours':168,'restore_rehearsal_limit_seconds':3600}
        raw=json.dumps(report,sort_keys=True).encode()
        key='recovery-evidence/'+stamp.strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex+'.json'
        archive.client.put_object(Bucket=BUCKET,Key=key,Body=raw,
                                  Metadata={'sha256':hashlib.sha256(raw).hexdigest()})
        archive._verify(key,hashlib.sha256(raw).hexdigest(),len(raw))
        return report
    finally:
        src.dispose();dst.dispose()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if a.output.exists():p.error('Output already exists')
    try:
        source=connection_url(os.getenv('NEON_PMS_DATABASE_URL',''),destination=True)
        target=os.environ['PMS_RECOVERY_TEST_URL']
        local_target(target)
        archive=PrivateArchive.from_env()
        with tempfile.TemporaryDirectory(prefix='pms-isolated-recovery-') as d:
            r=rehearse(source,target,archive,Path(d))
        code=0
    except Exception as e:
        r={'status':'BLOCKED','error_code':str(e) if isinstance(e,CopyBlocked) else 'RECOVERY_CHECK_FAILED',
           'error_type':type(e).__name__,'source_writes':False,'production_restore':False}
        code=2
    a.output.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r))
    return code


if __name__=='__main__':raise SystemExit(main())
