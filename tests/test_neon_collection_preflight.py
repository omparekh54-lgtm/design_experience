import json
from pathlib import Path
import pytest
from scripts import check_neon_collection as cli


def test_preflight_missing_destination_is_safe_and_does_not_probe_sources(monkeypatch, tmp_path):
    monkeypatch.delenv('NEON_PMS_DATABASE_URL', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(cli, 'CloudCollector', lambda **kw: pytest.fail('source accessed before configuration'))
    output = tmp_path/'receipt.json'
    assert cli.main(['--output',str(output)]) == 2
    report = json.loads(output.read_text())
    assert report['error_code'] == 'DESTINATION_URL_MISSING'
    assert not report['database_writes'] and not report['campaign_started']


def test_preflight_malformed_url_does_not_echo_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('NEON_PMS_DATABASE_URL','bad-private-password-value')
    output = tmp_path/'receipt.json'
    assert cli.main(['--output',str(output)]) == 2
    assert 'private-password' not in output.read_text()+capsys.readouterr().out


def test_preflight_refuses_overwriting_prior_evidence(tmp_path):
    output = tmp_path/'receipt.json'; output.write_text('prior evidence')
    with pytest.raises(SystemExit): cli.main(['--output',str(output)])
    assert output.read_text() == 'prior evidence'
