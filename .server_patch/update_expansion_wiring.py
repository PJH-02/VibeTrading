from pathlib import Path

root = Path(__file__).resolve().parents[1]
p=root/'vibetrading/runtime.py'
s=p.read_text(encoding='utf8').replace('"binance-perp", "bitget-perp", "bybit-perp"',
    '"binance-perp", "bitget-perp", "bybit-perp", "arcus-perp", "grvt-perp"')
p.write_text(s,encoding='utf8')
p=root/'vibetrading/cli.py'
s=p.read_text(encoding='utf8').replace('"binance-perp", "bitget-perp", "bybit-perp", "ls-',
    '"binance-perp", "bitget-perp", "bybit-perp", "arcus-perp", "grvt-perp", "ls-')
s=s.replace('"toss", "kiwoom", "binance", "hyperliquid"]',
    '"toss", "kiwoom", "binance", "hyperliquid", "arcus", "grvt"]')
p.write_text(s,encoding='utf8')
p=root/'config/crypto_runtime.yaml'
s=p.read_text(encoding='utf8').replace('strategies: []',
    '  - provider: arcus-perp\n    symbols: "@crypto"\n  - provider: grvt-perp\n    symbols: "@crypto"\nstrategies: []')
p.write_text(s,encoding='utf8')
