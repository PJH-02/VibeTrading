"""Vendor only original deterministic feature functions; record source hashes."""
import ast
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
repo = root.parent / 'Future_Trading'
out = root / 'vibetrading' / 'etf_reference.py'
selected = {
    'jehyeong/intraday/code/collect_intraday_data.py': ['validate', 'unique_bars', 'aggregate_30m'],
    'jehyeong/intraday/code/backtest_intraday_momentum.py': ['make_features', 'weights'],
    'beomgu/overnight_lab/topics/r3_d2.py': ['rolling_z1'],
}
content = ['"""Original Future_Trading feature formulas, vendored by prepare_etf_reference.py."""',
    'import numpy as np\nimport pandas as pd',
    'FIELDS = ["open", "high", "low", "close", "volume"]',
    'LOOKBACK, VOL_TARGET = 14, .02',
    'TIMES = pd.date_range("2000-01-01 09:30", "2000-01-01 15:00", freq="30min").strftime("%H:%M").tolist()',
    'MARKETS = {"KOSPI200": {"signal": "069500", "long": ("069500", "122630"), "short": (), "ceiling": 2.}, "KOSDAQ150": {"signal": "229200", "long": ("229200",), "short": ("251340",), "ceiling": 1.}}']
hashes = {}
for relative, names in selected.items():
    source = (repo / relative).read_text(encoding='utf-8')
    tree = ast.parse(source)
    hashes[relative] = hashlib.sha256((repo / relative).read_bytes()).hexdigest()
    for name in names:
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        content.append(ast.get_source_segment(source, node))
result = '\n\n'.join(content) + '\n'
ast.parse(result)
out.write_text(result, encoding='utf-8')
selection = json.loads((repo / 'jehyeong/intraday/results/final/selected_rules.json').read_text(encoding='utf-8'))
manifest = {'source_sha256': hashes, 'rules': selection['markets'], 'C3': 'C3|vt30|ETF', 'C3_execution': 'confirmed open signal; subsequent tradable price, never the already-known auction open'}
(root / 'config/etf_strategy_provenance.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'output': str(out), 'functions': selected}))
