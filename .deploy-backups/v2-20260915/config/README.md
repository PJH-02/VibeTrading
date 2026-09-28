# Universe configuration

`universe.yaml` is the single manual instrument list. Add a stock, ETF, or
Hyperliquid tokenized security under `instruments` and list the providers that
should collect it:

```yaml
instruments:
  - symbol: "005930"
    asset_class: equity
    providers: [kiwoom, toss]
  - symbol: "xyz:SKHYNIX"
    asset_class: tokenized_equity
    providers: [hyperliquid]
```

For Toss US instruments use the `US:` prefix (for example `US:SPY`). Domestic
symbols may be six-digit codes. The runtime entries in `runtime.yaml` use
`symbols: "@universe"` for Kiwoom and Toss, so changes are picked up when a
collector starts or is restarted.

Expiring futures/options and Binance top-market-cap symbols are not copied
here: their resolver policies are in the `dynamic` section and are refreshed
from provider masters/catalogs. Provider credentials and transport settings
remain in `.env`; they are intentionally separate from this instrument policy.

The legacy external Hyperliquid TOML directory is only a fallback when no
Hyperliquid symbols are configured in this file.
