import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { isAuthorizedViewer, sanitizeObservability, streamHasCapacity, validObservability, validPnlRow } from "../lib/observability.ts";

const row = {
  key: "a".repeat(64), id: "a".repeat(64), strategy_id: "alpha",
  account_scope: "b".repeat(24), mode: "paper", currency: "KRW",
  realized_pnl: "1000.25", unrealized_pnl: "0", fees: "1.25", funding: "0",
  net_pnl: "999", fee_convention: "gross realized; net = realized + unrealized - fees + funding",
  pnl_method_version: "paper-ledger-v1", equity_curve: [], position_count: 0,
  working_order_count: 0, trades: 2, asof_ms: 1800000000000,
  mark_asof_ms: null, mark_provenance: "NO_OPEN_POSITION", stale: false,
  attribution_status: "COMPLETE", status: "AVAILABLE", evidence: "PAPER_SIMULATION",
};
const base = { pnl: [row], latency: [], latency_status: "AVAILABLE", latency_error: null,
  native_status: "NOT_CONFIGURED", native_error: null,
  etf_status: "NOT_CONFIGURED", etf_error: null,
  end_to_end_ms: null, end_to_end_status: "SEGMENTED" };

assert.equal(validPnlRow(row), true);
assert.equal(validObservability(base), true);
assert.equal(validObservability({ ...base, pnl: [{ ...row, account_scope: "private-account-id" }] }), false);
assert.equal(validObservability({ ...base, pnl: [{ ...row, net_pnl: "NaN" }] }), false);
assert.equal(validObservability({ ...base, end_to_end_ms: 8 }), false);
assert.equal(validObservability({ ...base, pnl: Array(101).fill(row) }), false);

const metric = { stage: "market_apply", source_id: "feed", strategy_id: "alpha",
  account_scope: "b".repeat(24), mode: "paper", clock_domain: "proc-domain",
  trace_ref: null, windows: Object.fromEntries(["1m", "5m", "15m"].map(key =>
    [key, { p50_ms: 1, p95_ms: 2, p99_ms: 3, n: 10, coverage_ms: 60000 }])),
  source_latest_age_ms: 5, last_observation_age_ms: 5, queue_depth: 0,
  oldest_queue_age_ms: 0, errors: 0, drops: 0, seen: 10, sampled: 10, sample_out: 0,
  stale: false, private_payload: "do-not-store" };
const snapshot = { schema_version: 1, kind: "runtime_latency", process_id: "proc-1",
  clock_domain: "proc-domain", generated_ms: 1800000000000, metrics: [metric],
  strategies: [{ strategy_id: "alpha", account_scope: "b".repeat(24), mode: "paper",
    currency: "USDT", status: "RUNNING", updated_ms: 1800000000000, secret: "do-not-store" }],
  resources: { pid: 17, rss_bytes: 1000, private_key: "do-not-store" },
  end_to_end_ms: null, end_to_end_status: "SEGMENTED" };
const sanitized = sanitizeObservability({ ...base, pnl: [{ ...row, api_token: "do-not-store" }], latency: [snapshot] });
assert.equal(validObservability(sanitized), true);
assert.equal(JSON.stringify(sanitized).includes("do-not-store"), false);

// Exercise the strict TypeScript validator against a nonempty snapshot emitted
// by the frozen Python telemetry module, including lifecycle, windows, and resources.
const pythonSnapshot = String.raw`
import json, time, tempfile, sqlite3, os
from operations.dashboard.latency import observe, observe_strategy, snapshot
from operations.dashboard.pnl import native_ledger_pnl_projection
now = time.monotonic_ns()
observe("dashboard_projection", started_ns=now-2000000, ended_ns=now,
        source_id="fixture", strategy_id="alpha", account_scope='["bybit","acct","perpetual","testnet"]',
        mode="testnet", trace_id="trace-fixture", queue_depth=2,
        oldest_queue_age_ns=4000000, source_age_ns=3000000)
observe_strategy("alpha", '["bybit","acct","perpetual","testnet"]', "testnet", "RUNNING", currency="USDT")
fd,path = tempfile.mkstemp(suffix=".sqlite3")
os.close(fd)
with sqlite3.connect(path) as db:
  db.executescript("CREATE TABLE owner_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE execution_intents(intent_id TEXT PRIMARY KEY,strategy_id TEXT,state TEXT,filled TEXT,fees TEXT,payload TEXT,reserve_currency TEXT,updated_ns INTEGER,contract_multiplier TEXT); CREATE TABLE execution_fills(fill_id TEXT PRIMARY KEY,intent_id TEXT,fingerprint TEXT);")
  db.execute("INSERT INTO owner_metadata VALUES('scope',?)", ('["bybit","acct","perpetual","live"]',))
  for name,side,qty in [('a','buy','1'),('b','buy','2'),('c','sell','1')]:
    payload=json.dumps({'instrument':{'venue':'bybit','asset_class':'crypto_perp','currency':'USDT','std_symbol':'BTCUSDT'},'side':side})
    db.execute("INSERT INTO execution_intents VALUES(?,?,?,?,?,?,?,?,?)", (name,'alpha','FILLED',qty,'0',payload,'USDT',1,'1'))
  for name,qty,price in [('a','1','1'),('b','2','2'),('c','1','3')]:
    db.execute("INSERT INTO execution_fills VALUES(?,?,?)", (name,name,json.dumps([name,qty,price,'0'],separators=(',',':'))))
rows=native_ledger_pnl_projection([path], product_metadata=[{'scope':['bybit','acct','perpetual','live'],'instrument':'bybit:crypto_perp:BTCUSDT','currency':'USDT','fee_currency':'USDT','settlement_currency':'USDT','multiplier':'1','funding_applicable':False,'funding_complete':True,'source':'fixture'}], marks=[{'account_scope':'["bybit","acct","perpetual","live"]','key':'bybit:crypto_perp:BTCUSDT','currency':'USDT','price':'2','last_event_ms':100,'provider':'fixture'}], generated_ms=200)
os.unlink(path)
print(json.dumps({'latency':snapshot(),'pnl':rows[0]}))
`;
const integration = JSON.parse(execFileSync("python3", ["-c", pythonSnapshot], {
  cwd: process.cwd(), env: { ...process.env, PYTHONPATH: "src" }, encoding: "utf8",
}));
const emitted = integration.latency;
const withEmitted = sanitizeObservability({ ...base, latency: [emitted] });
assert.equal(validObservability(withEmitted), true);
assert.equal(withEmitted.latency[0].metrics[0].mode, "sandbox");
assert.equal(withEmitted.latency[0].strategies[0].currency, "USDT");
assert.equal(withEmitted.latency[0].metrics[0].windows["1m"].n, 1);
assert.equal(integration.pnl.net_pnl, "2");
assert.equal(validPnlRow(integration.pnl), true);
const projectionThroughValidator = sanitizeObservability({ ...base, pnl: [...base.pnl, integration.pnl] });
assert.equal(validObservability(projectionThroughValidator), true);

// Fake 60-second cadence with three independent viewers. One drains each frame;
// two remain slow. The server helper never offers them an extra queued frame.
const viewers = [{ desiredSize: 1, delivered: 0, pending: 0 },
  { desiredSize: 0, delivered: 0, pending: 0 }, { desiredSize: 0, delivered: 0, pending: 0 }];
for (let fakeNow = 0; fakeNow < 60000; fakeNow += 2000) {
  for (const viewer of viewers) {
    if (streamHasCapacity(viewer.desiredSize)) viewer.delivered += 1;
    else viewer.pending = 0;
    assert.equal(viewer.pending <= 1, true);
  }
}
assert.equal(viewers[0].delivered, 30);
assert.equal(viewers[1].delivered, 0);
assert.equal(viewers[2].delivered, 0);
assert.equal(streamHasCapacity(null), false);
assert.equal(streamHasCapacity(0), false);

const sameOrigin = new Request("https://dashboard.example/api/state", {
  headers: { "oai-authenticated-user-id": "viewer-1", origin: "https://dashboard.example" },
});
const originOmitted = new Request("https://dashboard.example/api/state", {
  headers: { "oai-authenticated-user-id": "viewer-1" },
});
const anonymous = new Request("https://dashboard.example/api/state");
const crossOrigin = new Request("https://dashboard.example/api/state", {
  headers: { "oai-authenticated-user-id": "viewer-1", origin: "https://attacker.example" },
});
assert.equal(isAuthorizedViewer(sameOrigin), true);
assert.equal(isAuthorizedViewer(originOmitted), true);
assert.equal(isAuthorizedViewer(anonymous), false);
assert.equal(isAuthorizedViewer(crossOrigin), false);

console.log("observability schema and viewer authorization checks passed");
