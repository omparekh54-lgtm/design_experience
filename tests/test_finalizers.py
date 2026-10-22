"""Adversarial acceptance tests: failures must never leave a valid certificate."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import hashlib
import json
from types import SimpleNamespace

import pytest

from packages.pms.backtest import BacktestResult
from packages.pms.backtest_matrix import MatrixRunner, Period, fingerprint, matrix_plan, plain
from packages.pms.config import load_strategy_config
from packages.pms.integrity_checks import CHECKS, REVIEWS
from packages.pms.matrix_data import report_lock
from scripts import finalize_step1_core as step1
from scripts import finalize_step10 as step10
from scripts.finalize_state import run_finalizer, write_state

START, END = date(2023, 1, 1), date(2024, 12, 31)
CFG = load_strategy_config()
CELLS = matrix_plan(CFG, [Period('CUSTOM', START, END)])


@pytest.mark.parametrize('status', ['ERROR', 'NOT_RUN', 'RUNNING', 'UNKNOWN', None])
@pytest.mark.parametrize('code', ['SECTOR_PRESENCE', 'SOURCE_PROVENANCE', 'INDEPENDENT_CALENDAR', 'EARNINGS_KNOWABILITY'])
def test_execution_errors_cannot_be_resolved_by_any_policy(code, status):
    row = {'code': code, 'status': status}
    assert step1.unresolved_checks({'checks': [row]}, {code}) == [row]


@pytest.mark.parametrize('status', ['FAIL', 'REVIEW_REQUIRED'])
def test_executed_findings_require_explicit_resolution(status):
    row = {'code': 'SECTOR_PRESENCE', 'status': status}
    assert step1.unresolved_checks({'checks': [row]}, set()) == [row]
    assert step1.unresolved_checks({'checks': [row]}, {'SECTOR_PRESENCE'}) == []


def audit():
    return {'finished': True, 'summary': {'complete': True},
            'requested_scope': {'start': str(START), 'end': str(END)},
            'checks': [{'code': code, 'status': 'PASS'} for code in
                       ['SCHEMA_CONTRACT', *(c.code for c in CHECKS), *(r[0] for r in REVIEWS)]]}


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unfinished', 'wrong_scope', 'incomplete'])
def test_truncated_or_wrong_scope_audit_is_never_certifiable(mutation):
    value = audit()
    if mutation == 'missing': value['checks'].pop()
    if mutation == 'duplicate': value['checks'].append(value['checks'][0])
    if mutation == 'unfinished': value['finished'] = False
    if mutation == 'incomplete': value['summary']['complete'] = False
    if mutation == 'wrong_scope': value['requested_scope']['end'] = '2025-01-01'
    with pytest.raises(RuntimeError): step1.audit_contract(value, START, END)


def test_complete_audit_contract():
    step1.audit_contract(audit(), START, END)


@pytest.mark.parametrize('result', [2, 130])
def test_failed_rerun_invalidates_old_success_without_deleting_checkpoints(tmp_path, result):
    cert, dependent = tmp_path/'step1.json', tmp_path/'step10.json'
    checkpoint = tmp_path/'prices.json'
    for path in (cert, dependent): write_state(path, {'status': 'STEP1_COMPLETE'})
    checkpoint.write_text('preserved')
    def attempt():
        assert json.loads(cert.read_text())['status'] == 'STEP1_RUNNING'
        return result
    assert run_finalizer(attempt, cert, 'STEP1', dependent=dependent) == result
    assert json.loads(cert.read_text())['status'] == 'STEP1_BLOCKED'
    assert json.loads(dependent.read_text())['status'] == 'STEP10_BLOCKED'
    assert checkpoint.read_text() == 'preserved'


def test_failed_rerun_preserves_precise_repair_inventory(tmp_path):
    cert = tmp_path/'step1.json'
    def attempt():
        write_state(cert, {'status': 'STEP1_BLOCKED', 'pending_tasks': [{'check': 'REAL_GAP'}]})
        return 2
    run_finalizer(attempt, cert, 'STEP1')
    assert json.loads(cert.read_text())['pending_tasks'] == [{'check': 'REAL_GAP'}]


def test_crash_cannot_leave_a_success_certificate(tmp_path):
    cert = tmp_path/'step10.json'
    write_state(cert, {'status': 'STEP10_COMPLETE'})
    def crash(): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): run_finalizer(crash, cert, 'STEP10')
    assert json.loads(cert.read_text())['status'] == 'STEP10_BLOCKED'


def test_concurrent_finalizer_cannot_invalidate_owner_certificate(tmp_path):
    cert = tmp_path/'step1.json'
    write_state(cert, {'status': 'OWNER'})
    with report_lock(tmp_path/'finalization'):
        assert run_finalizer(lambda: pytest.fail('must not run'), cert, 'STEP1') == 2
    assert json.loads(cert.read_text())['status'] == 'OWNER'


def preflight():
    return {'mode': 'CHECK_ONLY', 'scope': 'HISTORICAL_MASTER', 'dataset_fingerprint': 'data', 'ready_cells': len(CELLS),
            'cells': [{'key': c.key, 'blockers': []} for c in CELLS]}


@pytest.mark.parametrize('mutation', ['empty', 'missing', 'duplicate', 'blocked', 'absent_blockers', 'sample', 'wrong_mode', 'wrong_count'])
def test_preflight_requires_every_exact_cell_and_explicit_clear_gates(mutation):
    value = preflight()
    if mutation == 'empty': value.update(cells=[], ready_cells=0)
    if mutation == 'missing': value['cells'].pop(); value['ready_cells'] -= 1
    if mutation == 'duplicate': value['cells'][-1] = value['cells'][0]
    if mutation == 'blocked': value['cells'][0]['blockers'] = ['missing_prices']
    if mutation == 'absent_blockers': value['cells'][0].pop('blockers')
    if mutation == 'sample': value['scope'] = 'EXPLICIT_SYMBOL_SAMPLE'
    if mutation == 'wrong_mode': value['mode'] = 'PLAN_ONLY'
    if mutation == 'wrong_count': value['ready_cells'] -= 1
    with pytest.raises(RuntimeError): step10.validate_preflight(value, CELLS)


def test_complete_preflight_passes():
    step10.validate_preflight(preflight(), CELLS)


@pytest.mark.parametrize('state', ['[]', 'null', 'true', '"bad"', '{broken'])
def test_malformed_checkpoint_state_is_quarantined_not_reused(tmp_path, monkeypatch, state):
    from packages.pms import matrix_data
    monkeypatch.setattr(matrix_data, 'code_fingerprint', lambda: 'code')
    for name in ('RUN1', 'RUN2', 'STATE', 'STEP1_CERT'):
        monkeypatch.setattr(step10, name, tmp_path/(name+'.json'))
    config = tmp_path/'cfg.yaml'; config.write_text('config')
    step10.STEP1_CERT.write_text('step1')
    step10.STATE.write_text(state)
    step10.RUN1.write_text('old evidence')
    step10.prepare_checkpoints(config)
    assert not step10.RUN1.exists()
    assert list(tmp_path.glob('RUN1.json.stale-*'))
    assert isinstance(json.loads(step10.STATE.read_text()), dict)


@pytest.mark.parametrize('script,arguments', [
    (step1, ['--matrix-start', '2024-01-01']),
    (step1, ['--start', '2023-02-01']),
    (step1, ['--end', '2024-01-01']),
    (step1, ['--end', '2999-01-01']),
    (step10, ['--start', '2024-01-01']),
    (step10, ['--end', '2025-01-01']),
])
def test_wrong_acceptance_scope_fails_before_database_or_certificate_mutation(monkeypatch, script, arguments):
    monkeypatch.setattr('sys.argv', ['finalize.py', *arguments])
    monkeypatch.setattr(script, 'db_engine', lambda: pytest.fail('database must not be touched'))
    with pytest.raises(SystemExit): script.main()


@pytest.fixture
def replay(tmp_path):
    cells = CELLS[:1]
    dataset = {'fingerprint': 'data', 'scope': 'HISTORICAL_MASTER',
               'universe': [{'id': 1, 'instrument_id': 'NSE:A'}]}
    path = tmp_path/'matrix.json'
    def execute(cell, cfg):
        result = BacktestResult(10000, 10000, 0, 0, 0, 0, [], [], 0,
                               [(date(2023,1,2), 10000)], 10000, 0)
        journal = tmp_path/'journal.json'
        body = json.dumps(plain(asdict(result)), sort_keys=True, allow_nan=False).encode()
        journal.write_bytes(body)
        return result, {'evidence_file': journal.name, 'evidence_sha256': hashlib.sha256(body).hexdigest()}
    runner = MatrixRunner(CFG, cells, dataset, path, executor=execute,
                          gate=lambda *a: [], code_version='code')
    runner.run()
    return path, cells


def test_actual_matrix_report_and_trade_journal_pass(replay):
    path, cells = replay
    assert step10.validate_replay(path, CFG, cells, 'data', 'code')['summary']['status'] == 'COMPLETED'


@pytest.mark.parametrize('mutation', ['missing_cell', 'tampered_metrics', 'missing_journal', 'changed_journal', 'wrong_config', 'wrong_code', 'wrong_data', 'absent_journal_reference'])
def test_replay_claimed_success_cannot_hide_missing_or_tampered_evidence(replay, mutation):
    path, cells = replay
    report = json.loads(path.read_text())
    if mutation == 'missing_cell': report['cells'] = []
    if mutation == 'tampered_metrics': report['cells'][0]['metrics']['ending_equity'] = 20000
    if mutation == 'missing_journal': (path.parent/'journal.json').unlink()
    if mutation == 'changed_journal': (path.parent/'journal.json').write_text('{}')
    if mutation == 'wrong_config': report['config_hash'] = 'wrong'
    if mutation == 'wrong_code': report['code_version'] = 'wrong'
    if mutation == 'wrong_data': report['dataset']['fingerprint'] = 'wrong'
    if mutation == 'absent_journal_reference':
        row = report['cells'][0]; row['diagnostics'] = {}
        row['evidence_hash'] = fingerprint({k:v for k,v in row.items() if k != 'evidence_hash'})
    path.write_text(json.dumps(report))
    with pytest.raises((RuntimeError, ValueError)):
        step10.validate_replay(path, CFG, cells, 'data', 'code')


def test_interrupted_matrix_child_is_terminated_and_reaped(monkeypatch, tmp_path):
    class Child:
        stopped = False
        reaped = False
        def poll(self): return 2 if self.stopped else None
        def terminate(self): self.stopped = True
        def wait(self, **kw): self.reaped = True
    child = Child()
    monkeypatch.setattr(step10.subprocess, 'Popen', lambda *a, **kw: child)
    def interrupt(*a): raise KeyboardInterrupt()
    monkeypatch.setattr(step10, '_monitor_matrix', interrupt)
    with pytest.raises(KeyboardInterrupt):
        step10.matrix_subprocess(['fake'], tmp_path/'report', SimpleNamespace(), 0, 100, 'replay')
    assert child.stopped and child.reaped


def test_sector_snapshots_never_backdate_or_extend_stale_history(monkeypatch):
    from scripts import backfill_historical_sector_intervals as sectors
    def forbidden(): pytest.fail('current snapshot is newer than requested cutoff')
    monkeypatch.setattr(sectors, 'fetch_current_memberships', forbidden)
    old = [{'index_name': 'Nifty IT', 'symbol': 'A', 'valid_from': '2020-01-01',
            'valid_to': '', 'source': 'press_release', 'source_url': 'dated-source'}]
    rows, metadata = sectors.reconcile_current(old, date(2026,10,21), observed_on=date(2026,10,22))
    assert rows[0]['valid_to'] == '2026-05-16'
    assert len(rows) == 1
    assert metadata['unknown_history_from'] == '2026-05-16'
    assert old[0]['valid_to'] == ''
    monkeypatch.setattr(sectors, 'fetch_current_memberships', lambda: (
        {'Nifty IT': {'A'}}, {'Nifty IT': {'source_uri': 'official-current'}}))
    rows, _ = sectors.reconcile_current(old, date(2026,10,22), observed_on=date(2026,10,22))
    assert rows[1]['valid_from'] == '2026-10-22'
    assert rows[1]['source'] == 'snapshot'


def test_conflicting_benchmark_duplicates_are_not_silently_overwritten():
    from scripts.ingest_module_e_benchmarks import fetch_window
    source = SimpleNamespace(fetch_historical_index_data=lambda *a, **kw: [
        {'EOD_TIMESTAMP': '02-JAN-2023', 'EOD_CLOSE_INDEX_VAL': 100},
        {'EOD_TIMESTAMP': '02-JAN-2023', 'EOD_CLOSE_INDEX_VAL': 200}])
    with pytest.raises(RuntimeError, match='conflicting duplicate'):
        fetch_window(source, 'NIFTY50', START, date(2023,1,31))


def test_complete_one_day_benchmark_window_can_resume():
    from scripts.ingest_module_e_benchmarks import _existing_month_is_usable
    day = date(2023,1,2)
    assert _existing_month_is_usable({day}, day, day)


def test_step10_two_complete_fixture_matrices_produce_certificate(tmp_path, monkeypatch):
    from packages.pms import matrix_data
    for name, filename in [('ARTIFACT_ROOT', ''), ('STEP10_DIR', 'step10'),
                           ('STEP1_CERT', 'step1_certificate.json'), ('STEP10_CERT', 'step10_certificate.json'),
                           ('RUN1', 'step10/matrix-run1.json'), ('RUN2', 'step10/matrix-run2.json'),
                           ('STATE', 'step10/state.json')]:
        monkeypatch.setattr(step10, name, tmp_path/filename)
    monkeypatch.setattr(step10, 'ROOT', tmp_path)
    step10.STEP1_CERT.write_text('{"status":"fixture"}')
    config = tmp_path/'strategy.yaml'
    import yaml
    config.write_text(yaml.safe_dump(CFG))
    monkeypatch.setattr(step10, 'require_step1', lambda *a: {'audit_run_id': 'audit', 'matrix_dataset_fingerprint': 'data'})
    monkeypatch.setattr(step10, 'research_config', lambda: config)
    monkeypatch.setattr(matrix_data, 'code_fingerprint', lambda: 'code')
    monkeypatch.setattr(step1, 'matrix_dataset_fingerprint', lambda *a: 'data')
    monkeypatch.setattr(step10, 'survivorship_audit', lambda *a: {'pass': True, 'missing_ids': []})
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(step10, 'db_engine', lambda: engine)
    # Required suite presence is checked before invoking it.
    suite = tmp_path/'tests'; suite.mkdir()
    for name in ['test_backtest_matrix.py', 'test_matrix_data_api.py', 'test_matrix_replay_integration.py',
                 'test_unified_research_replay.py', 'test_historical_execution_consistency.py',
                 'test_historical_dataset.py', 'test_finalizers.py']:
        (suite/name).touch()
    monkeypatch.setattr(step10, 'run_checked', lambda *a, stdout_path=None, **kw:
                        stdout_path.write_text(json.dumps(preflight())) if stdout_path else 0)
    calls = []
    def matrix(cmd, path, *a):
        calls.append(cmd)
        dataset = {'fingerprint': 'data', 'scope': 'HISTORICAL_MASTER',
                   'universe': [{'id': 1, 'instrument_id': 'NSE:A'}]}
        def execute(cell, cfg):
            value = BacktestResult(10000, 10000, 0, 0, 0, 0, [], [], 0,
                                   [(date(2023,1,2), 10000)], 10000, 0)
            body = json.dumps(plain(asdict(value)), sort_keys=True).encode()
            journal = path.parent/(path.stem+'_'+cell.key+'.json')
            journal.write_bytes(body)
            return value, {'evidence_file': journal.name, 'evidence_sha256': hashlib.sha256(body).hexdigest()}
        runner = MatrixRunner(CFG, CELLS, dataset, path, executor=execute, gate=lambda *a: [], code_version='code')
        runner.run()
    monkeypatch.setattr(step10, 'matrix_subprocess', matrix)
    monkeypatch.setattr('sys.argv', ['finalize_step10.py'])
    assert step10.main() == 0
    cert = json.loads(step10.STEP10_CERT.read_text())
    assert cert['status'] == 'STEP10_COMPLETE' and cert['cells'] == len(CELLS)
    assert cert['reproducibility'] == 'PASS'
    assert len(calls) == 2 and '--no-persist' in calls[1] and '--no-persist' not in calls[0]
    assert cert['scope'] == {'start': str(START), 'end': str(END)}


def test_step1_prerequisite_failure_never_starts_expensive_backfills(tmp_path, monkeypatch):
    monkeypatch.setattr(step1, 'ARTIFACT_ROOT', tmp_path)
    monkeypatch.setattr(step1, 'CERTIFICATE', tmp_path/'step1_certificate.json')
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(step1, 'db_engine', lambda: engine)
    def fail(*a): raise RuntimeError('Financial period identity migration 012 is required before source writes')
    monkeypatch.setattr(step1, 'fail_fast_historical_prerequisites', fail)
    monkeypatch.setattr(step1, 'run', lambda *a, **kw: pytest.fail('backfill must not run'))
    monkeypatch.setattr('sys.argv', ['finalize_step1.py'])
    assert step1.main() == 2
    assert json.loads(step1.CERTIFICATE.read_text())['status'] == 'STEP1_BLOCKED'


@pytest.mark.parametrize('mode', ['complete', 'sql_error', 'data_changed', 'missing_check'])
def test_step1_full_orchestration_certifies_only_finished_stable_audit(tmp_path, monkeypatch, mode):
    from packages.pms import matrix_data
    from packages.pms.data_integrity import write_report
    monkeypatch.setattr(step1, 'ROOT', tmp_path)
    monkeypatch.setattr(step1, 'ARTIFACT_ROOT', tmp_path/'artifacts')
    monkeypatch.setattr(step1, 'CERTIFICATE', tmp_path/'artifacts/step1_certificate.json')
    monkeypatch.setattr(matrix_data, 'code_fingerprint', lambda: 'code')
    monkeypatch.setattr(step1, 'db_engine', lambda: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(step1, 'fail_fast_historical_prerequisites', lambda *a: {
        'pre2024_asm_rows': 9951, 'ambiguous_master_identities': 0, 'financial_period_identity': 'PASS'})
    monkeypatch.setattr(step1, 'repair_reference_history', lambda *a: {'fixture': True})
    evidence = {'legacy_surveillance_source_backed': True, 'historical_sector_window': {'rows': 1},
                'price_sources': [{'source': 'NSE_UDIFF', 'rows': 100}]}
    monkeypatch.setattr(step1, 'evidence_snapshot', lambda *a: evidence)
    policies = {k: {'pass': True} for k in step1.EVIDENCE_REVIEW_CODES | {'FAIL_CLOSED_MISSING_REFERENCE_POLICY'}}
    monkeypatch.setattr(step1, 'review_resolution_pack', lambda *a: policies)
    monkeypatch.setattr(step1, 'final_review_pack', lambda *a: {
        k: {'pass': True, 'evidence': {}} for k in step1.STATIC_REVIEWS})
    audited = {'scope': 'HISTORICAL_MASTER', 'universe': [{'id': 1}]}
    calls = []
    def dataset(engine, start, end):
        calls.append((start, end))
        if mode == 'data_changed' and len(calls) > 2: return 'changed'
        return fingerprint(audited) if start.year == 2018 else 'matrix'
    monkeypatch.setattr(step1, 'matrix_dataset_fingerprint', dataset)
    def run(cmd, label, **kw):
        if 'scripts/run_data_integrity_audit.py' in cmd:
            path = tmp_path/cmd[cmd.index('--report')+1]
            report = audit()
            report['requested_scope']['start'] = '2018-01-01'
            report.update(run_id='audit', dataset=audited)
            if mode == 'sql_error':
                next(r for r in report['checks'] if r['code'] == 'SECTOR_PRESENCE')['status'] = 'ERROR'
            if mode == 'missing_check': report['checks'].pop()
            write_report(report, path)
            path.with_suffix('.repair.json').write_text('{"pending_tasks": 1}')
        return 0
    monkeypatch.setattr(step1, 'run', run)
    monkeypatch.setattr('sys.argv', ['finalize_step1.py', '--end', str(END)])
    result = step1.main()
    cert = json.loads(step1.CERTIFICATE.read_text())
    assert result == (0 if mode == 'complete' else 2)
    assert cert['status'] == ('STEP1_COMPLETE' if mode == 'complete' else 'STEP1_BLOCKED')
    if mode == 'complete':
        assert cert['pending_tasks'] == [] and cert['pending_repairs'] == 0
        assert cert['matrix_scope'] == {'start': str(START), 'end': str(END)}
    if mode == 'sql_error':
        assert any(r['check'] == 'SECTOR_PRESENCE' and r['status'] == 'ERROR' for r in cert['pending_tasks'])


def certified_step1(tmp_path, monkeypatch):
    from packages.pms.data_integrity import write_report
    from packages.pms import matrix_data
    monkeypatch.setattr(matrix_data, 'code_fingerprint', lambda: 'code')
    monkeypatch.setattr(step10, 'ROOT', tmp_path)
    monkeypatch.setattr(step10, 'STEP1_CERT', tmp_path/'step1.json')
    report = audit(); report['run_id'] = 'audit'
    path = tmp_path/'audit.json'; write_report(report, path)
    cert = {'status': 'STEP1_COMPLETE', 'pending_repairs': 0, 'pending_tasks': [],
            'matrix_scope': {'start': str(START), 'end': str(END)}, 'scope': {'start': str(START), 'end': str(END)},
            'matrix_dataset_fingerprint': 'data', 'code_fingerprint': 'code', 'audit_run_id': 'audit',
            'audit_report': 'audit.json', 'audit_sha256': step10.sha256(path),
            'final_review_pack': {c: {'pass': True} for c in step1.STATIC_REVIEWS},
            'policy_review_pack': {c: {'pass': True} for c in step1.EVIDENCE_REVIEW_CODES | {
                'FAIL_CLOSED_MISSING_REFERENCE_POLICY', 'EARNINGS_ARCHIVE_PRESENCE',
                'EARNINGS_KNOWABILITY', 'CORPORATE_ACTION_VALUES', 'CORPORATE_ACTION_CONFLICTS'}},
            'evidence': {'legacy_surveillance_source_backed': True, 'historical_sector_window': {'rows': 1}}}
    step10.STEP1_CERT.write_text(json.dumps(cert))
    return cert


def test_step10_accepts_complete_bound_step1_certificate(tmp_path, monkeypatch):
    cert = certified_step1(tmp_path, monkeypatch)
    assert step10.require_step1(START, END) == cert


@pytest.mark.parametrize('mutation', ['pending', 'code', 'missing_review', 'failed_review', 'audit_changed', 'audit_identity', 'scope'])
def test_step10_rejects_inconsistent_or_stale_step1_certificate(tmp_path, monkeypatch, mutation):
    cert = certified_step1(tmp_path, monkeypatch)
    if mutation == 'pending': cert['pending_tasks'] = [{'check': 'UNRESOLVED'}]
    if mutation == 'code': cert['code_fingerprint'] = 'old'
    if mutation == 'missing_review': cert['final_review_pack'].pop('INDEPENDENT_CALENDAR')
    if mutation == 'failed_review': cert['final_review_pack']['INDEPENDENT_CALENDAR']['pass'] = False
    if mutation == 'audit_changed': (tmp_path/'audit.json').write_text('{}')
    if mutation == 'audit_identity': cert['audit_run_id'] = 'other'
    if mutation == 'scope': cert['matrix_scope']['end'] = '2025-01-01'
    step10.STEP1_CERT.write_text(json.dumps(cert))
    with pytest.raises((RuntimeError, ValueError)):
        step10.require_step1(START, END)


@pytest.mark.parametrize('raw', ['[]', 'null', 'true', '"bad"', '{broken'])
def test_malformed_local_backfill_checkpoints_fall_back_to_database(tmp_path, raw):
    from scripts.backfill_official_historical_prices import _checkpoint
    from scripts.backfill_official_historical_financials import _load_checkpoint
    path = tmp_path/'checkpoint.json'; path.write_text(raw)
    assert _checkpoint(path, START, END) is None
    assert _load_checkpoint(path, START, END, 0, 8) is None


def test_finalizer_cannot_quarantine_an_active_matrix_report(tmp_path, monkeypatch):
    for name in ('RUN1', 'RUN2', 'STATE', 'STEP1_CERT'):
        monkeypatch.setattr(step10, name, tmp_path/(name+'.json'))
    step10.RUN1.write_text('active checkpoint')
    with report_lock(step10.RUN1):
        with pytest.raises(RuntimeError, match='Another matrix'):
            step10.prepare_checkpoints(tmp_path/'config.yaml')
    assert step10.RUN1.read_text() == 'active checkpoint'
