# Market data export and analysis

The server export contains only active manifest files. Compaction inputs marked
`superseded`, temporary files, credentials, and order state are excluded.

## Server-side snapshot

For a race-free snapshot, stop only the maintenance worker first. Collectors,
NATS, and the storage writer can keep running. Then run a one-off maintenance
container, which is the only service that receives the read-only Ncloud
credentials:

```bash
docker compose --project-name vibetrading --env-file /etc/vibetrading/compose.env stop maintenance
docker compose --project-name vibetrading --env-file /etc/vibetrading/compose.env run --rm -e PYTHONPATH=/app maintenance \
  python scripts/export_market_snapshot.py --output-root /data/exports --workers 4
docker compose --project-name vibetrading --env-file /etc/vibetrading/compose.env start maintenance
```

The command writes an extracted directory, a `tar.gz`, and a SHA-256 sidecar
under `/srv/vibetrading-data/runtime_data/exports` on the VM host.

## Windows analysis after extraction

```powershell
py -m pip install -r requirements-analysis.txt
py scripts\analyze_market_export.py `
  E:\VibeTradingData\vibetrading-market-YYYYMMDDTHHMMSSZ
```

Generated outputs:

- `by_asset/<provider>__<venue>__<symbol>/<event_type>.parquet`: consolidated data
- `by_asset/.../*.head.csv`: first 100 rows for quick inspection
- `assets.csv`: asset/event inventory
- `qa_summary.csv` and `qa_report.json`: missing-data and validity diagnostics
- `plots/<asset>/`: price, best bid/ask, and spread plots

Load any consolidated asset as a pandas DataFrame:

```python
from pathlib import Path
from scripts.analyze_market_export import load_asset_dataframe

analysis = Path(r"E:\VibeTradingData\vibetrading-market-...-analysis\by_asset")
# Use an exact directory name listed in analysis\by_asset\assets.csv.
df = load_asset_dataframe(analysis, "provider__venue__symbol", "order_book")
print(df.head())
```

Order-book gaps are reported as candidates rather than definite packet loss.
The collector stores at most one snapshot per second and intentionally does not
create a row in a second where no provider update arrived. Absolute price
accuracy must be compared with an independent exchange/reference feed; the
script checks internal OHLC, spread, quantity, ordering, timestamp, and
trade-versus-latest-quote consistency.
