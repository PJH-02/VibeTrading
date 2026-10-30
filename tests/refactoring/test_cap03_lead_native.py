"""Lead adverse checks use actual normalized native field conventions."""
from copy import deepcopy
import pytest
from execution.perp_execution import PerpScope
from execution.trading_contracts import validate_native_account_facts
from test_cap03_native_risk import NOW, SCOPE, account_facts, product_row


def check(facts, products, scope=SCOPE, identity='native-account-fixture'):
    return validate_native_account_facts(facts,products,scope,account_version='v7',
        expected_native_account_id=identity,now_ns=NOW,max_age_ns=100)


@pytest.mark.parametrize('target',['account','product'])
def test_empty_raw_response_is_not_source_evidence(target):
    facts=account_facts();products={'BTCUSDT':product_row()}
    if target=='account':facts['normalized']['raw']={}
    else:products['BTCUSDT']['raw']={}
    assert check(facts,products)['risk_ready'] is False


def test_explicit_native_address_cannot_disagree_with_identity_envelope():
    scope=PerpScope('hyperliquid','linear_perpetual','sandbox','alias','USDC')
    expected='0x'+'1'*40
    facts=account_facts(scope)
    facts['normalized'].update(currency='USDC',account_address='0x'+'2'*40)
    facts['native_identity']['native_account_id']=expected
    products={'BTCUSDT':product_row(scope,quote_currency='USDC',settlement_currency='USDC',collateral_currency='USDC')}
    assert check(facts,products,scope,expected)['risk_ready'] is False


def test_valid_output_is_detached_from_caller_facts():
    facts=account_facts();products={'BTCUSDT':product_row()}
    out=check(facts,products)
    assert out['risk_ready'] is True
    facts['normalized']['equity']='0';products['BTCUSDT']['raw']['symbol']='FOREIGN'
    assert out['facts']['reported']['equity']==1000
    assert out['facts']['products']['BTCUSDT']['raw']['symbol']=='BTCUSDT'
