import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.data_layer.action_identity import validate_action_identities
from packages.data_layer.cloud_sources import SourceError
from packages.pms import cloud_collection as module
from tests.test_cloud_collection import Engine, NOW
from tests.test_cloud_collection import document


def reviewed_actions():
    reviews = json.loads((Path(module.__file__).parents[1] / 'data_layer' /
                          'reviewed_action_identities.json').read_text())
    actions = [dict(symbol=r['symbol'], isin=r['previous_isin'],
                    ex_date=date.fromisoformat(r['ex_date']),
                    action_type=r['action_type'], raw=r['raw']) for r in reviews]
    known = {r['symbol']: {'isin':r['current_isin']} for r in reviews}
    return actions, known


def test_only_exact_reviewed_corporate_actions_link_to_current_identity():
    actions, known = reviewed_actions()
    original = copy.deepcopy(actions)
    receipt = validate_action_identities(actions, known)
    assert len(receipt) == len(actions) == 8 and actions == original
    assert all(r['previous_isin'] != r['current_isin'] for r in receipt)


@pytest.mark.parametrize('change', ['payload', 'date', 'current_isin', 'old_isin', 'type'])
def test_action_review_never_authorizes_other_records_or_identities(change):
    actions, known = reviewed_actions()
    a = actions[0]
    if change == 'payload':
        a['raw']['subject'] += ' revised'
    elif change == 'date':
        a['ex_date'] = date(2026,11,1)
    elif change == 'current_isin':
        known[a['symbol']]['isin'] = 'INE000000000'
    elif change == 'old_isin':
        a['isin'] = 'INE000000000'
    else:
        a['action_type'] = 'OTHER'
    with pytest.raises(SourceError, match='action_isin_mismatch'):
        validate_action_identities(actions, known)


def test_current_isin_needs_no_exception():
    assert validate_action_identities([{'symbol':'TEST','isin':'INE000000000'}],
                                     {'TEST':{'isin':'INE000000000'}}) == []


def test_reviewed_elecon_dividend_retains_fractional_rupee_amount():
    actions, known = reviewed_actions()
    raw = next(a['raw'] for a in actions if a['symbol'] == 'ELECON')
    parsed = module.parse_actions(document([raw]), NOW)[0]
    assert parsed['cash_amount'] == 0.5


def test_reviewed_techm_dividend_preserves_source_terms_and_rejects_changes():
    actions, known = reviewed_actions()
    action = next(a for a in actions if a['symbol'] == 'TECHM')
    parsed = module.parse_actions(document([action['raw']]), NOW)[0]
    assert parsed['cash_amount'] == 15 and parsed['ex_date'] == date(2026, 10, 23)
    assert parsed['raw']['isin'] == 'INE669C01028'
    assert validate_action_identities([parsed], known)[0]['current_isin'] == 'INE669C01036'
    parsed['raw'] = {**parsed['raw'], 'subject': 'Interim Dividend - Rs 16 Per Share'}
    with pytest.raises(SourceError, match='action_isin_mismatch'):
        validate_action_identities([parsed], known)


def test_failed_reference_stage_preserves_completed_stage_receipts(monkeypatch):
    def core(self, day, **kwargs):
        self.core_stages = {'prices': {'complete':True}}
        return {'status':'SUCCESS','stages':self.core_stages}
    def references(self, day, **kwargs):
        self.reference_stages = {'financials':{'processed':100,'remaining':12}}
        raise SourceError('action_isin_mismatch')
    monkeypatch.setattr(module.CloudCollector, 'core', core)
    monkeypatch.setattr(module.CloudCollector, 'references', references)
    r = module.run_collection(Engine(), now=NOW,
                             http=SimpleNamespace(documents=[]))
    assert r['status'] == 'FAILED' and r['complete'] is False
    assert r['stages']['prices']['complete'] is True
    assert r['stages']['financials']['remaining'] == 12


def test_irfc_review_does_not_extend_to_changed_dividend():
    actions,known=reviewed_actions()
    a=next(a for a in actions if a['symbol']=='IRFC')
    assert validate_action_identities([a],known)[0]['current_isin']=='INE053F01010'
    a['raw']['subject']='Interim Dividend - Rs 1.16 Per Share'
    with pytest.raises(SourceError,match='action_isin_mismatch'):
        validate_action_identities([a],known)
