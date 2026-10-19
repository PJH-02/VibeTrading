# Market-data export QA — 2026-09-14

## Export integrity

- Export: `vibetrading-market-20260914T130416Z`
- Active Parquet files: 1,843
- Rows: 2,775,722
- Uncompressed Parquet bytes: 236,412,234
- Archive bytes: 189,041,454
- Archive SHA-256: `c474817d406d6d9b8529228cf41c26b4da40dbe2f60287f44b41d4b34705bf58`
- Sources: 191 local VM files and 1,652 Ncloud-only files
- Reconstructed outputs: 67 assets, 114 asset/event Parquet files, 110 plots

The reconstructed row total exactly matches the export manifest row total.
Every input file was checked against its manifest byte count and SHA-256 before
the archive was created. The downloaded archive hash also matches the server
sidecar.

## Event totals

| Provider | Event | Rows |
|---|---|---:|
| Hyperliquid | 1-minute bar | 11,857 |
| Hyperliquid | funding rate | 270 |
| KIS | order book | 545,099 |
| KIS | trade | 188,695 |
| Kiwoom | order book | 317,243 |
| Kiwoom | trade | 1,277,613 |
| Toss | order book | 214,421 |
| Toss | trade | 220,524 |

## Quality findings

### Healthy checks

- No negative price or quantity was found in trades.
- No invalid OHLC relation, negative volume, or unclosed Hyperliquid bar was found.
- No missing Hyperliquid funding value was found.
- No bid/ask level ordering error or non-positive book level quantity was found.
- No negative source-to-receive latency was found.
- KIS books had no duplicate sampled timestamp. Toss books also had none.
- Hyperliquid hourly funding was complete for all three symbols in the exported range.
- Hyperliquid one-minute bar candidate gaps: SKHYNIX 0, SKHY 2, SAMSUNG 24.

### Items requiring attention

1. The Parquet writer is far behind the collectors. At the server check, the
   `MARKET_DATA` stream held about 650,000 pending messages and 660 MB, while
   storage used one full CPU core. The export ends around 10:04–10:06 KST even
   though NATS continued receiving through 22:17 KST. Those pending records are
   durable in NATS but are not part of this Parquet export.
2. Kiwoom contains 85 duplicate `event_id` occurrences. Samples show the same
   REST seed trades replayed multiple times with different receive times and
   NATS sequences. This is real duplicate ingestion, not merely multiple trades
   in the same second.
3. Toss contains two duplicate `event_id` values, and rows sharing an ID can
   have different price or quantity values. That means the event-ID construction
   or mutation order needs investigation before the ID is treated as a unique key.
4. Kiwoom books contain two seconds with two snapshots for the same asset,
   violating the intended per-asset maximum of one snapshot per second. They
   occurred for `069500` and `229200` at the same sampled timestamp.
5. Empty book sides were rare but present: KIS had 123 empty-bid and 149
   empty-ask rows; Kiwoom had 92 of each; Toss had 38 empty-bid and 12 empty-ask
   rows. Toss additionally had six crossed and seven locked books. These should
   be checked around reconnect/resynchronization boundaries.
6. Low apparent book coverage on illiquid KIS options is not proof of packet
   loss. The collector intentionally emits no row when there was no provider
   update. Liquid Kiwoom books showed roughly 95.6–98.3% candidate one-second
   coverage; Toss SPY showed roughly 74.9%; liquid KIS contracts ranged roughly
   89.6–96.8%.

## Price sanity checks

The latest persisted values were internally plausible and had valid book
ordering. At the check time, public Hyperliquid `xyz` mid prices were close to
the last persisted values:

| Mapping | Last persisted | Public mid at check |
|---|---:|---:|
| `xyz:SMSN` / `SAMSUNG-USDC` | 186.15 | 181.08 |
| `xyz:SKHY` / `SKHY-USDC` | 179.79 | 175.195 |
| `xyz:SKHX` / `SKHYNIX-USDC` | 1,274.00 | 1,240.95 |

This comparison supports symbol/decimal-scale plausibility but is not a
tick-for-tick accuracy proof because the persisted snapshot was about twelve
hours behind the live check. Absolute accuracy requires a time-aligned
independent reference feed.

## Output layout

- `by_asset/assets.csv`: inventory and exact directory names
- `by_asset/<provider>__<venue>__<symbol>/<event>.parquet`: consolidated DataFrame source
- `by_asset/.../<event>.head.csv`: first 100 rows
- `qa_summary.csv`: per-asset/event numeric checks
- `qa_report.json`: complete machine-readable report
- `plots/<asset>/`: price and order-book/spread plots

Use `load_asset_dataframe()` from `scripts/analyze_market_export.py` to load a
consolidated event file into a timestamp-sorted pandas DataFrame.
