from pathlib import Path
p=Path(__file__).resolve().parents[1]/'vibetrading/cli.py'
s=p.read_text(encoding='utf8').replace('choices=["binance-perp","bitget-perp","bybit-perp"]',
    'choices=["binance-perp","bitget-perp","bybit-perp","arcus-perp","grvt-perp"]')
s=s.replace('"collectors-bybit-perp", "storage"',
    '"collectors-bybit-perp", "collectors-arcus-perp", "collectors-grvt-perp", "storage"')
p.write_text(s,encoding='utf8')
