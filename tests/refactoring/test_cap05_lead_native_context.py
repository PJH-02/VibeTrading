"""Source-owned different contract sizes and immutable two-leg factual context."""
from decimal import Decimal
import pytest
from cap05_source_fixture import build_native_group
from test_cap05_native_integration import coordinator_for,close_fixture

@pytest.mark.asyncio
async def test_source_literal_different_multipliers_and_both_leg_context_are_preserved(tmp_path):
    f=build_native_group(tmp_path,binance_qty='0.1',bybit_qty='1.0');g=coordinator_for(f)
    f['registry'].binding('bybit-perp').adapter._metadata['BTCUSDT.P']['multiplier']='0.1'
    contexts=[]
    def source(event,context):
        view=context.execution['execution_group']
        assert view['group_id']==f['plan'].actions[0].group_id
        assert len(view['legs'])==2
        assert view['cross_ledger_atomic_commit'] is False
        with pytest.raises(TypeError):view['state']='forged'
        with pytest.raises(TypeError):view['legs'][0]['current']['filled']='forged'
        contexts.append(view)
        return None
    try:
        r=await g.execute(f['plan'],registry=f['registry'],readiness=f['readiness'],evidence=f['evidence'],on_event=source)
        assert r['state']=='ACKNOWLEDGED'
        assert len(contexts)==2, 'source must receive factual BOTH-leg context, not silently swallow missing groupcontext'
        current={leg['scope_key']:leg['current'] for leg in r['legs']}
        a,b=f['plan'].actions
        assert Decimal(current[a.scope.key]['quantity'])==Decimal('0.1')
        assert Decimal(current[b.scope.key]['quantity'])==Decimal('1.0')
        assert Decimal(current[a.scope.key]['contract_multiplier'])==1
        assert Decimal(current[b.scope.key]['contract_multiplier'])==Decimal('0.1')
        for x in current.values():
            assert x['valuation_currency']=='USDT'
            assert x['settlement_currency']=='USDT'
        assert Decimal(f['bybit_transport'].post_payloads[0]['qty'])==Decimal('1.0')
        assert f['bybit_owner'].ledger.reserved()==f['binance_owner'].ledger.reserved()==Decimal('10.02')
        assert r['economic_neutrality']=='NOT_ASSESSED'
    finally:await close_fixture(f)
