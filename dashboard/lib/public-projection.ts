import { createHash } from "node:crypto";

const hash = (value: unknown) => createHash("sha256").update(String(value)).digest("hex");
export const publicScope = (owner: string, rawScope = owner) => /^[0-9a-f]{24}$/i.test(rawScope) ? rawScope : hash(`vt-account:${owner}:${rawScope}`).slice(0, 24);
const scalar = (value: any) => value === null || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value)) || (typeof value === "string" && value.length <= 256);
const pick = (value: any, keys: readonly string[]) => Object.fromEntries(keys.filter(k => value?.[k] !== undefined && scalar(value[k])).map(k => [k, value[k]]));
const numeric = (value: any) => typeof value === "number" ? Number.isFinite(value) : typeof value === "string" && value.length <= 128 && /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value);
const curve = (value: any) => Array.isArray(value) ? value.slice(0, 1440).map((point: any) => ({ ...pick(point, ["ts", "currency", "asof", "status"]), ...(numeric(point?.net_pnl) || point?.net_pnl === null ? { net_pnl: point.net_pnl } : {}) })) : [];
const windows = (value: any) => value && typeof value === "object" ? Object.fromEntries(["1m", "5m", "15m"].filter(key => value[key] !== undefined).map(key => [key, Object.fromEntries(["p50_ms", "p95_ms", "p99_ms", "n", "coverage_ms", "errors"].filter(field => Number.isFinite(value[key]?.[field])).map(field => [field, value[key][field]]))])) : {};
const levels = (value: any) => Array.isArray(value) ? value.slice(0, 10).flatMap((entry: any) => Array.isArray(entry) && entry.length === 2 && entry.every(numeric) ? [[entry[0], entry[1]]] : []) : [];
const projectOrder = (value: any) => {
  const order = pick(value, ["section", "mode", "strategy", "symbol", "venue", "side", "status", "quantity_unit", "price_unit", "created_ms", "updated_ms"]);
  for (const field of ["quantity", "weight_pct", "price"]) if (numeric(value?.[field])) order[field] = value[field];
  return order;
};

export function publicSnapshot(snapshot: any, owner: string) {
  return {
    schema_version: 1, generated_ms: Number.isSafeInteger(snapshot?.generated_ms) ? snapshot.generated_ms : null,
    received_ms: Number.isSafeInteger(snapshot?.received_ms) ? snapshot.received_ms : null,
    connected: snapshot?.connected === true,
    markets: Array.isArray(snapshot?.markets) ? snapshot.markets.slice(0, 512).map((m: any) => ({
      ...pick(m, ["key", "venue", "symbol", "section", "asset_class", "currency", "last_event_ms", "observed_delay_ms", "quote_kind"]),
      gap_reason: m?.gap_reason ? "UNAVAILABLE" : null,
      ...(numeric(m?.price) || m?.price === null ? { price: m.price } : {}),
      book: m?.book ? { ...pick(m.book, ["receive_ms", "source_ms"]), bids: levels(m.book.bids), asks: levels(m.book.asks) } : null,
      trades: Array.isArray(m?.trades) ? m.trades.slice(0, 40).map((t: any) => ({ ...pick(t, ["side", "source_ms", "receive_ms"]), ...(numeric(t?.price) ? { price: t.price } : {}), ...(numeric(t?.quantity) ? { quantity: t.quantity } : {}), id: hash(`trade:${t?.id ?? ""}`).slice(0, 24) })) : [],
    })) : [],
    strategies: Array.isArray(snapshot?.strategies) ? snapshot.strategies.slice(0, 100).map((s: any) => ({
      ...pick(s, ["id", "name", "section", "mode", "currency", "symbol", "status", "running", "updated_ms", "realized_pnl", "unrealized_pnl", "pnl_basis"]),
      account: publicScope(owner, s?.account ?? "unknown"),
    })) : [],
    timefolio: Array.isArray(snapshot?.timefolio) ? snapshot.timefolio.slice(0, 50).map((a: any) => ({
      ...pick(a, ["status", "updated_ms", "turnover_pct", "realized_pnl", "nav"]),
      positions: Array.isArray(a?.positions) ? a.positions.slice(0, 100).map((p: any) => ({ ...(typeof p?.ticker === "string" && /^[A-Za-z0-9._-]{1,32}$/.test(p.ticker) ? { ticker: p.ticker } : {}), ...(numeric(p?.qty) ? { qty: p.qty } : {}), ...(numeric(p?.weight_pct) ? { weight_pct: p.weight_pct } : {}) })) : [],
      id: hash(`vt-timefolio:${owner}:${a?.id ?? a?.label ?? "unknown"}`).slice(0, 24), label: "Private account",
    })) : [],
    orders: Array.isArray(snapshot?.orders) ? snapshot.orders.slice(0, 1000).map((o: any) => ({
      ...projectOrder(o),
      id: hash(`vt-order:${owner}:${o?.id ?? ""}`).slice(0, 24), account: publicScope(owner, o?.account ?? "unknown"),
    })) : [],
    observability: snapshot?.observability ? {
      pnl: Array.isArray(snapshot.observability.pnl) ? snapshot.observability.pnl.slice(0, 100).map((r: any) => ({
        ...pick(r, ["key", "id", "strategy_id", "mode", "currency", "realized_pnl", "unrealized_pnl", "fees", "funding", "net_pnl", "fee_convention", "pnl_method_version", "position_count", "working_order_count", "trades", "asof_ms", "mark_asof_ms", "mark_provenance", "stale", "attribution_status", "status", "evidence", "runtime_status", "runtime_updated_ms", "unallocated_amount"]),
        equity_curve: curve(r?.equity_curve),
        account_scope: publicScope(owner, r?.account_scope ?? "unknown"),
      })) : [],
      latency: Array.isArray(snapshot.observability.latency) ? snapshot.observability.latency.slice(0, 32).map((row: any) => ({
        ...pick(row, ["schema_version", "kind", "clock_domain", "generated_ms", "end_to_end_ms", "end_to_end_status", "snapshot_stale"]),
        process_id: hash(`vt-process:${owner}:${row?.process_id ?? "unknown"}`).slice(0, 24),
        metrics: Array.isArray(row.metrics) ? row.metrics.slice(0, 256).map((metric: any) => ({
          ...pick(metric, ["stage", "clock_domain", "source_latest_age_ms", "queue_depth", "oldest_queue_age_ms", "errors", "drops", "seen", "sampled", "sample_out", "last_observation_age_ms", "stale"]),
          windows: windows(metric?.windows),
          source_id: hash(`vt-source:${owner}:${metric?.source_id ?? "unknown"}`).slice(0, 24),
          strategy_id: hash(`vt-strategy:${owner}:${metric?.strategy_id ?? "unknown"}`).slice(0, 24),
          account_scope: publicScope(owner, metric?.account_scope ?? "unknown"), trace_ref: null,
        })) : [], strategies: [], resources: pick(row.resources, ["process_cpu_seconds", "rss_bytes", "cgroup_cpu_seconds", "cgroup_memory_bytes"]),
      })) : [],
      latency_status: snapshot.observability.latency_status, latency_error: null,
      native_status: snapshot.observability.native_status, native_error: null,
      etf_status: snapshot.observability.etf_status, etf_error: null,
      end_to_end_ms: null, end_to_end_status: "SEGMENTED",
    } : undefined,
    read_only: true,
  };
}

export function publicPnl(rows: any[], owner: string) {
  const fields = ["key", "id", "strategy_id", "mode", "currency", "realized_pnl", "unrealized_pnl", "fees", "funding", "net_pnl", "fee_convention", "pnl_method_version", "equity_curve", "position_count", "working_order_count", "trades", "asof_ms", "mark_asof_ms", "mark_provenance", "stale", "attribution_status", "status", "evidence", "runtime_status", "runtime_updated_ms", "unallocated_amount"];
  return { rows: rows.slice(0, 1440).map((row: any) => ({ ...pick(row, fields), equity_curve: curve(row?.equity_curve), account_scope: publicScope(owner, row?.account_scope ?? "unknown") })) };
}

export function publicOrders(rows: any[], owner: string) {
  return { rows: rows.slice(0, 200).map((row: any) => {
    const value = typeof row === "string" ? JSON.parse(row) : row;
    return { ...projectOrder(value), id: hash(`vt-order:${owner}:${value?.id ?? ""}`).slice(0, 24), account: publicScope(owner, value?.account ?? "unknown") };
  }) };
}
