"""Frozen CAP-00 evidence and required-scope invariants; no runtime activation."""
from functools import lru_cache
from pathlib import Path
import ast
import hashlib
import io
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / 'docs/tickets/CAP-00-capability-matrix.json'


def matrix():
    return json.loads(MATRIX.read_text())


@lru_cache(maxsize=1)
def frozen_sources():
    m = matrix()
    paths = list(m['source_files'])
    p = subprocess.run(['git', 'cat-file', '--batch'], cwd=ROOT,
        input=''.join(m['code_source_sha'] + ':' + path + '\n' for path in paths).encode(),
        capture_output=True, check=True)
    stream, output = io.BytesIO(p.stdout), {}
    for path in paths:
        header = stream.readline().split()
        assert len(header) == 3 and header[1] == b'blob'
        data = stream.read(int(header[2]))
        assert stream.read(1) == b'\n'
        assert hashlib.sha256(data).hexdigest() == m['source_files'][path]
        output[path] = data
    return output


def node(path, symbol):
    nodes = ast.parse(frozen_sources()[path]).body
    for name in symbol.split('.'):
        matches = [n for n in nodes if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        assert len(matches) == 1
        selected = matches[0]
        nodes = selected.body
    return selected


def test_required_scope_preserves_default_venues_modes_and_cross_venue_code():
    m = matrix(); rows = m['capabilities']
    assert m['new_product_code_files'] == m['blanket_connector_rebuilds'] == 0
    assert m['requirements_default_not_narrowed'] and not m['global_feature_completion']
    perp = [r for r in rows if r['product'] == 'linear_perpetual']
    assert {(r['venue'], r['environment']) for r in perp} == {
        (v, e) for v in ('binance', 'bybit', 'grvt', 'arcus', 'hyperliquid') for e in ('sandbox', 'live')}
    assert all(r['code_required'] for r in perp)
    kis = [r for r in rows if r['venue'] == 'kis' and r['product'] == 'KR_CASH_ETF']
    assert {(r['environment'], r['strategy_id']) for r in kis} == {
        (e, s) for e in ('mock', 'mock2') for s in ('C3', 'KP', 'KD')}
    group = next(r for r in rows if r['id'] == 'CROSS-VENUE-REQUIRED-CODE')
    assert group['code_required'] and group['code_requirement'] == 'REQUIRED_CORE_CODE_EVEN_WITHOUT_ACTUAL_PAIR'
    assert not group['actual_live_pair_selected']
    toss = next(r for r in rows if r['id'] == 'TOSS-DATA')
    assert toss['code_required'] and toss['existing_data_collection_required'] and not toss['required_mutations']
    assert next(r for r in rows if r['id'] == 'TOSS-ORDERS-BLOCKED')['decision_status'] == 'UNSUPPORTED_BLOCKED'
    for identity in ('NH-NAMOO-FUTURE', 'NEW-CEX-FUTURE'):
        r = next(r for r in rows if r['id'] == identity)
        assert r['extension_seam_required'] and not r['real_api_code_required']
    assert not next(r for r in rows if r['id'] == 'KR350-OPT-IN')['code_required']


def test_required_rows_have_real_sources_tests_owners_and_separate_external_gates():
    m = matrix(); gaps = {g['id']: g for g in m['function_level_gaps']}
    assert len({r['id'] for r in m['capabilities']}) == len(m['capabilities'])
    ticket_ids = {t['task_id'] for t in json.loads((ROOT/'docs/final/05_TASK_STATUS.json').read_text())['tickets']}
    for r in m['capabilities']:
        for field in ('venue', 'product', 'environment', 'account_scope', 'strategy_id', 'execution_mode',
                      'claim', 'decision_owner', 'decision_status', 'order_mode', 'transport_owner',
                      'private_stream', 'reconcile_path', 'external_gate'):
            assert r[field], (r['id'], field)
        assert not r['activation_authorized'] and not r['production_verified']
        if r['code_required']:
            assert r['source_functions'] and r['local_test_nodeids']
            assert all(ref in m['functions'] for ref in r['source_functions'])
            assert all(ref in m['accepted_local_test_evidence'] for ref in r['local_test_nodeids'])
        for gap_id in r['gaps']:
            assert gap_id in gaps and set(gaps[gap_id]['owner_tickets']) <= ticket_ids
            assert gaps[gap_id]['local_or_external'] != 'EXTERNAL_ONLY'
    for ref in m['accepted_local_test_evidence'].values():
        tree = ast.parse((ROOT/ref['path']).read_bytes())
        assert any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == ref['function'] for n in ast.walk(tree))


def test_frozen_function_asts_and_module_hashes_match_the_recorded_git_snapshot():
    m = matrix(); assert len(frozen_sources()) == len(m['source_files'])
    for value in m['functions'].values():
        n = node(value['path'], value['symbol'])
        assert (n.lineno, n.end_lineno) == (value['line'], value['end_line'])
        assert hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest() == value['ast_sha256']


def test_kiwoom_contract_freezes_missing_lifecycle_without_com_substitution():
    m = matrix(); rest = [r for r in m['capabilities'] if r['id'].startswith('KIWOOM-REST-')]
    assert len(rest) == 4 and all(r['code_required'] for r in rest)
    assert {(r['product'], r['environment']) for r in rest} == {
        (p, e) for p in ('KR_CASH_STOCK', 'KR_CASH_ETF') for e in ('mock', 'live')}
    status = node('src/execution/brokers.py', 'KiwoomBrokerAdapter.order_status')
    assert any(isinstance(n, ast.Constant) and n.value == 'NOT_IMPLEMENTED' for n in ast.walk(status))
    klass = node('src/execution/brokers.py', 'KiwoomBrokerAdapter')
    methods = {n.name for n in klass.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not methods & {'cancel_order', 'list_open_orders', 'get_fills', 'reconcile'}
    assert all('BLOCKED_API_SPEC_REQUIRED' in r['api_contract_status'] for r in rest)
    com = next(r for r in m['capabilities'] if r['id'] == 'KIWOOM-WINDOWS-COM')
    assert com['environment'] == 'windows_com' and not com['code_required']
    assert com['claim'] == 'LEGACY_UNVERIFIED'
    assert com['decision_status'] == 'LEGACY_SEPARATE_OS_NOT_REST_SUBSTITUTE'


def test_spot_and_perp_frozen_paths_are_not_aliased():
    spot = node('src/execution/brokers.py', 'BinanceBrokerAdapter.submit_order')
    perp = node('src/execution/perp_execution.py', 'BinancePerpAdapter.submit_with_permit')
    assert any(isinstance(n, ast.Constant) and n.value == '/api/v3/order' for n in ast.walk(spot))
    assert any(isinstance(n, ast.Constant) and n.value == '/fapi/v1/order' for n in ast.walk(perp))
    rows = matrix()['capabilities']
    assert any(r['product'] == 'crypto_spot' for r in rows)
    assert all(r['settlement_currency'] for r in rows if r['product'] == 'linear_perpetual')


def test_mock_mock2_paper_and_live_claims_cannot_be_interchanged():
    rows = matrix()['capabilities']
    for r in rows:
        if r['venue'] == 'kis':
            assert r['claim'] == r['environment'].upper() and r['claim'] not in {'PAPER', 'TESTNET', 'LIVE'}
        if r['environment'] == 'live':
            assert r['claim'] == 'LIVE' and not r['local_integration_verified'] and not r['production_verified']
    paper = next(r for r in rows if r['id'] == 'GENERIC-PAPER')
    assert paper['environment'] == 'paper' and paper['claim'] == 'PAPER'
    dry = next(r for r in rows if r['id'] == 'TIMEFOLIO-LOCAL_DRY_RUN')
    assert dry['claim'] == 'CONTEST_DRY_RUN' and not dry['activation_authorized']
