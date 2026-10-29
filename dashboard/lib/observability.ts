export const MAX_PNL_ROWS = 100;
export const MAX_LATENCY_SNAPSHOTS = 32;
export const MAX_CURVE_POINTS = 1440;

const decimal = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;
const hexScope = /^[0-9a-f]{24}$/;
const pnlFields = ["key", "id", "strategy_id", "account_scope", "mode", "currency", "realized_pnl",
  "unrealized_pnl", "fees", "funding", "net_pnl", "fee_convention", "pnl_method_version",
  "equity_curve", "position_count", "working_order_count", "trades", "asof_ms", "mark_asof_ms",
  "mark_provenance", "stale", "attribution_status", "status", "evidence", "runtime_status",
  "runtime_updated_ms", "unallocated_amount"] as const;
const metricFields = ["stage", "source_id", "strategy_id", "account_scope", "mode", "clock_domain",
  "trace_ref", "windows", "source_latest_age_ms", "queue_depth", "oldest_queue_age_ms", "errors",
  "drops", "seen", "sampled", "sample_out", "last_observation_age_ms", "stale"] as const;
const resourceFields = ["pid", "process_cpu_seconds", "rss_bytes", "cgroup_cpu_seconds", "cgroup_memory_bytes",
  "cgroup_io_some_avg10", "cgroup_io_full_avg10", "sample_capacity_per_metric",
  "metric_capacity", "lock_contention_drops", "cardinality_drops", "invalid_drops",
  "sampling_period_ms", "retention_ms", "export_errors", "export_oversize_errors",
  "export_write_errors", "export_lock_errors"] as const;

function pick(value: any, fields: readonly string[]) {
  const result: Record<string, unknown> = {};
  for (const field of fields) if (value && Object.prototype.hasOwnProperty.call(value, field)) result[field] = value[field];
  return result;
}

export function isAuthorizedViewer(request: Request): boolean {
  const identity = request.headers.get("oai-authenticated-user-id");
  const origin = request.headers.get("origin");
  return typeof identity === "string" && identity.length > 0 && identity.length <= 256 &&
    (origin === null || origin === new URL(request.url).origin);
}

export function validPnlRow(row: any): boolean {
  return !!row && typeof row === "object" &&
    Object.keys(row).every((key) => pnlFields.includes(key as typeof pnlFields[number])) &&
    typeof row.key === "string" && row.key.length === 64 && /^[0-9a-f]{64}$/.test(row.key) &&
    row.id === row.key && typeof row.strategy_id === "string" && row.strategy_id.length <= 256 &&
    typeof row.account_scope === "string" && hexScope.test(row.account_scope) &&
    typeof row.mode === "string" && row.mode.length <= 32 &&
    typeof row.currency === "string" && row.currency.length <= 16 &&
    ["realized_pnl", "unrealized_pnl", "fees", "funding", "net_pnl"].every((field) =>
      row[field] === null || (typeof row[field] === "string" && row[field].length <= 256 && decimal.test(row[field]))) &&
    typeof row.fee_convention === "string" && row.fee_convention.length <= 256 &&
    typeof row.pnl_method_version === "string" && row.pnl_method_version.length <= 64 &&
    typeof row.equity_curve !== "undefined" && Array.isArray(row.equity_curve) && row.equity_curve.length <= MAX_CURVE_POINTS &&
    row.equity_curve.every((point: any) => point && Object.keys(point).every((key) => ["ts", "net_pnl", "currency", "asof", "status"].includes(key)) &&
      Number.isSafeInteger(point.ts) && (point.net_pnl === null || (typeof point.net_pnl === "string" && decimal.test(point.net_pnl))) &&
      typeof point.currency === "string" && point.currency.length <= 16 && typeof point.status === "string" && point.status.length <= 32) &&
    (row.unallocated_amount === undefined || row.unallocated_amount === null ||
      (typeof row.unallocated_amount === "string" && row.unallocated_amount.length <= 256 && decimal.test(row.unallocated_amount))) &&
    ["position_count", "working_order_count", "trades"].every((field) =>
      row[field] === null || (Number.isSafeInteger(row[field]) && row[field] >= 0)) &&
    (row.asof_ms === null || Number.isSafeInteger(row.asof_ms)) &&
    (row.mark_asof_ms === null || Number.isSafeInteger(row.mark_asof_ms)) &&
    typeof row.stale === "boolean" &&
    ["COMPLETE", "INCOMPLETE", "UNALLOCATED"].includes(row.attribution_status) &&
    ["AVAILABLE", "INCOMPLETE"].includes(row.status) &&
    ["PAPER_SIMULATION", "RUNTIME_REGISTRY", "NATIVE_EXECUTION_LEDGER", "ETF_JOURNAL", "LEGACY_SUMMARY"].includes(row.evidence) &&
    (row.runtime_status === undefined || (typeof row.runtime_status === "string" && row.runtime_status.length <= 48)) &&
    (row.runtime_updated_ms === undefined || row.runtime_updated_ms === null || Number.isSafeInteger(row.runtime_updated_ms));
}

export function sanitizeObservability(value: any): any | null {
  if (!value || typeof value !== "object" || !Array.isArray(value.pnl) || !Array.isArray(value.latency)) return null;
  const pnl = value.pnl.slice(0, MAX_PNL_ROWS).map((row: any) => {
    const sanitized = pick(row, pnlFields);
    if (Array.isArray(row?.equity_curve)) sanitized.equity_curve = row.equity_curve.slice(0, MAX_CURVE_POINTS).map((point: any) => pick(point, ["ts", "net_pnl", "currency", "asof", "status"]));
    return sanitized;
  });
  const latency = value.latency.slice(0, MAX_LATENCY_SNAPSHOTS).map((snapshot: any) => ({
    ...pick(snapshot, ["schema_version", "kind", "process_id", "clock_domain", "generated_ms", "end_to_end_ms", "end_to_end_status", "snapshot_stale"]),
    metrics: Array.isArray(snapshot?.metrics) ? snapshot.metrics.slice(0, 256).map((metric: any) => {
      const sanitized = pick(metric, metricFields);
      if (metric?.windows && typeof metric.windows === "object") {
        sanitized.windows = Object.fromEntries(["1m", "5m", "15m"].filter((window) => metric.windows[window] !== undefined).map((window) =>
          [window, pick(metric.windows[window], ["p50_ms", "p95_ms", "p99_ms", "n", "coverage_ms", "errors"])]));
      }
      return sanitized;
    }) : [],
    strategies: Array.isArray(snapshot?.strategies) ? snapshot.strategies.slice(0, 100).map((row: any) => pick(row,
      ["strategy_id", "account_scope", "mode", "currency", "status", "updated_ms"])) : [],
    resources: pick(snapshot?.resources, resourceFields),
  }));
  return {
    pnl, latency,
    latency_status: value.latency_status,
    latency_error: value.latency_error ?? null,
    native_status: value.native_status,
    native_error: value.native_error ?? null,
    etf_status: value.etf_status,
    etf_error: value.etf_error ?? null,
    end_to_end_ms: null,
    end_to_end_status: value.end_to_end_status,
  };
}

export function validObservability(value: any): boolean {
  if (!value || typeof value !== "object" || !Array.isArray(value.pnl) ||
      value.pnl.length > MAX_PNL_ROWS || !Array.isArray(value.latency) ||
      value.latency.length > MAX_LATENCY_SNAPSHOTS ||
      value.end_to_end_ms !== null || value.end_to_end_status !== "SEGMENTED") return false;
  if (!["AVAILABLE", "NOT_CONFIGURED", "UNAVAILABLE"].includes(value.latency_status)) return false;
  if (!["AVAILABLE", "NOT_CONFIGURED", "UNAVAILABLE"].includes(value.native_status)) return false;
  if (!["AVAILABLE", "NOT_CONFIGURED", "UNAVAILABLE"].includes(value.etf_status)) return false;
  if (value.latency_error !== null &&
      (typeof value.latency_error !== "string" || !/^[A-Za-z][A-Za-z0-9]{0,79}$/.test(value.latency_error))) return false;
  if (value.native_error !== null &&
      (typeof value.native_error !== "string" || !/^[A-Za-z][A-Za-z0-9]{0,79}$/.test(value.native_error))) return false;
  if (value.etf_error !== null &&
      (typeof value.etf_error !== "string" || !/^[A-Za-z][A-Za-z0-9]{0,79}$/.test(value.etf_error))) return false;
  if (!value.pnl.every(validPnlRow)) return false;
  return value.latency.every((snapshot: any) => {
    if (!snapshot || !Array.isArray(snapshot.metrics) || !Array.isArray(snapshot.strategies) ||
        Object.keys(snapshot).some((key) => !["schema_version", "kind", "process_id", "clock_domain", "generated_ms", "metrics", "strategies", "end_to_end_ms", "end_to_end_status", "resources", "snapshot_stale"].includes(key)) ||
        snapshot.schema_version !== 1 || snapshot.kind !== "runtime_latency" ||
        !Number.isSafeInteger(snapshot.generated_ms) || !Array.isArray(snapshot.metrics) ||
        snapshot.metrics.length > 256 ||
        snapshot.strategies.length > 100 || snapshot.end_to_end_ms !== null ||
        snapshot.end_to_end_status !== "SEGMENTED" ||
        Object.keys(snapshot.resources || {}).some((key) => !resourceFields.includes(key as typeof resourceFields[number]))) return false;
    const validWindow = (window: any) => window && Object.keys(window).every((key) => ["p50_ms", "p95_ms", "p99_ms", "n", "coverage_ms", "errors"].includes(key)) &&
      ["p50_ms", "p95_ms", "p99_ms"].every((key) => window[key] === null || (typeof window[key] === "number" && Number.isFinite(window[key]) && window[key] >= 0)) &&
      Number.isSafeInteger(window.n) && window.n >= 0 && typeof window.coverage_ms === "number" &&
      Number.isFinite(window.coverage_ms) && window.coverage_ms >= 0 &&
      (window.errors === undefined || (Number.isSafeInteger(window.errors) && window.errors >= 0));
    const validMetric = (metric: any) => metric && Object.keys(metric).every((key) => metricFields.includes(key as typeof metricFields[number])) &&
      typeof metric.stage === "string" && metric.stage.length <= 80 && typeof metric.source_id === "string" && metric.source_id.length <= 128 &&
      typeof metric.strategy_id === "string" && metric.strategy_id.length <= 256 && typeof metric.clock_domain === "string" &&
      metric.clock_domain === snapshot.clock_domain && metric.clock_domain.length <= 128 &&
      (metric.account_scope === "" || hexScope.test(metric.account_scope)) &&
      typeof metric.mode === "string" && metric.mode.length <= 32 &&
      (metric.trace_ref === null || (typeof metric.trace_ref === "string" && hexScope.test(metric.trace_ref))) &&
      ["source_latest_age_ms", "last_observation_age_ms", "queue_depth", "oldest_queue_age_ms"].every((key) =>
        metric[key] === null || (typeof metric[key] === "number" && Number.isFinite(metric[key]) && metric[key] >= 0)) &&
      metric.windows && Object.keys(metric.windows).every((key) => ["1m", "5m", "15m"].includes(key)) &&
      Object.values(metric.windows).every(validWindow) && ["errors", "drops", "seen", "sampled", "sample_out"].every((key) => Number.isSafeInteger(metric[key]) && metric[key] >= 0);
    const nullableResources = ["rss_bytes", "process_cpu_seconds", "cgroup_cpu_seconds", "cgroup_memory_bytes",
      "cgroup_io_some_avg10", "cgroup_io_full_avg10"];
    const validResources = Object.entries(snapshot.resources || {}).every(([key, entry]) =>
      key === "pid" ? Number.isSafeInteger(entry) :
        (nullableResources.includes(key) && entry === null) ? true :
          typeof entry === "number" && Number.isFinite(entry) && entry >= 0);
    const validStrategies = snapshot.strategies.every((row: any) => row &&
      Object.keys(row).every((key) => ["strategy_id", "account_scope", "mode", "currency", "status", "updated_ms"].includes(key)) &&
      typeof row.strategy_id === "string" && row.strategy_id.length <= 256 &&
      (row.account_scope === "" || hexScope.test(row.account_scope)) && typeof row.mode === "string" && row.mode.length <= 32 &&
      (row.currency === null || (typeof row.currency === "string" && row.currency.length <= 16)) &&
      typeof row.status === "string" && row.status.length <= 48 && Number.isSafeInteger(row.updated_ms));
    return typeof snapshot.process_id === "string" && snapshot.process_id.length <= 128 &&
      typeof snapshot.clock_domain === "string" && snapshot.clock_domain.length <= 128 &&
      (snapshot.snapshot_stale === undefined || typeof snapshot.snapshot_stale === "boolean") &&
      validResources && validStrategies && snapshot.metrics.every(validMetric);
  });
}

export function streamHasCapacity(desiredSize: number | null): boolean {
  return desiredSize !== null && desiredSize > 0;
}
