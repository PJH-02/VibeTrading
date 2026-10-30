#!/usr/bin/env node
"use strict";

// Bounded local-only authenticated HTTPS dashboard readers for PERF-01.
// This imports the repository's real TypeScript gateway/routes and SQL migration;
// its TLS clients connect only to the loopback server started below.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const https = require("node:https");
const { Readable } = require("node:stream");
const { AsyncLocalStorage } = require("node:async_hooks");
const { DatabaseSync } = require("node:sqlite");

function parseArgs(argv) {
  const values = new Map();
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    if (!["--source-root", "--typescript", "--cert", "--key", "--output",
          "--duration-seconds", "--interval-ms", "--telemetry-input",
          "--disconnect-after-seconds", "--disconnect-seconds"].includes(key)) {
      throw new Error("ARGUMENT");
    }
    if (values.has(key) || i + 1 >= argv.length || argv[i + 1].startsWith("--")) {
      throw new Error("ARGUMENT");
    }
    values.set(key, argv[++i]);
  }
  const required = ["--source-root", "--typescript", "--cert", "--key", "--output"];
  if (required.some(key => !values.has(key))) throw new Error("ARGUMENT");
  const durationSeconds = Number(values.get("--duration-seconds") || "120");
  const intervalMs = Number(values.get("--interval-ms") || "2000");
  const hasDisconnectAfter = values.has("--disconnect-after-seconds");
  const hasDisconnectDuration = values.has("--disconnect-seconds");
  const disconnectAfterSeconds = hasDisconnectAfter ? Number(values.get("--disconnect-after-seconds")) : null;
  const disconnectSeconds = hasDisconnectDuration ? Number(values.get("--disconnect-seconds")) : null;
  if (!Number.isSafeInteger(durationSeconds) || durationSeconds < 1 || durationSeconds > 3600 ||
      !Number.isSafeInteger(intervalMs) || intervalMs < 100 || intervalMs > 60_000) {
    throw new Error("ARGUMENT");
  }
  if (hasDisconnectAfter !== hasDisconnectDuration ||
      (hasDisconnectAfter && (!Number.isSafeInteger(disconnectAfterSeconds) || disconnectAfterSeconds < 1 ||
        !Number.isSafeInteger(disconnectSeconds) || disconnectSeconds < 1 || disconnectSeconds > 3600 ||
        disconnectAfterSeconds + disconnectSeconds + intervalMs / 1000 >= durationSeconds))) throw new Error("ARGUMENT");
  return {
    sourceRoot: path.resolve(values.get("--source-root")),
    typescript: path.resolve(values.get("--typescript")),
    cert: path.resolve(values.get("--cert")),
    key: path.resolve(values.get("--key")),
    output: path.resolve(values.get("--output")),
    telemetryInput: values.has("--telemetry-input")
      ? path.resolve(values.get("--telemetry-input")) : null,
    durationSeconds,
    intervalMs,
    disconnectAfterSeconds,
    disconnectSeconds,
  };
}

const args = parseArgs(process.argv.slice(2));
const dashboardRoot = fs.existsSync(path.join(args.sourceRoot, "dashboard", "lib"))
  ? path.join(args.sourceRoot, "dashboard")
  : args.sourceRoot;
const hashFile = file => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const sha256 = value => crypto.createHash("sha256").update(value).digest("hex");
const requiredFiles = [
  "lib/viewer-gateway.ts", "lib/server.ts", "lib/public-projection.ts",
  "lib/viewer-context.ts", "app/api/state/route.ts", "app/api/orders/route.ts",
  "app/api/pnl/route.ts", "app/api/observability/stream/route.ts",
  "drizzle/0000_slimy_tombstone.sql",
];
for (const relative of requiredFiles) {
  if (!fs.statSync(path.join(dashboardRoot, relative)).isFile()) throw new Error("SOURCE_MISSING");
}
if (!fs.statSync(args.typescript).isFile() || !fs.statSync(args.cert).isFile() ||
    !fs.statSync(args.key).isFile()) throw new Error("INPUT_MISSING");

const typescript = require(args.typescript);
const sqlite = new DatabaseSync(":memory:");
const migrationPath = path.join(dashboardRoot, "drizzle/0000_slimy_tombstone.sql");
sqlite.exec(fs.readFileSync(migrationPath, "utf8").replace(/--> statement-breakpoint/g, ""));
let sqlReads = 0;
let sqlWrites = 0;
const requestMode = new AsyncLocalStorage();
const sqlByMode = { public: { reads: 0, writes: 0 }, private_ingest: { reads: 0, writes: 0 }, other: { reads: 0, writes: 0 } };
const countSql = (kind) => {
  const mode = requestMode.getStore();
  const bucket = sqlByMode[mode] || sqlByMode.other;
  bucket[kind]++;
};
const db = {
  prepare(query) {
    let bound = [];
    const statement = {
      bind(...values) { bound = values; return statement; },
      async first() { sqlReads++; countSql("reads"); return sqlite.prepare(query).get(...bound) || null; },
      async all() { sqlReads++; countSql("reads"); return { results: sqlite.prepare(query).all(...bound) }; },
      async run() { sqlWrites++; countSql("writes"); return sqlite.prepare(query).run(...bound); },
    };
    return statement;
  },
  async batch(statements) {
    for (const statement of statements) await statement.run();
    return [];
  },
};

const token = crypto.randomBytes(32).toString("hex");
const serviceToken = crypto.randomBytes(32).toString("hex");
const env = {
  DB: db,
  VIEWER_TOKEN_SHA256: sha256(token),
  VIEWER_SESSION_SECRET: crypto.randomBytes(48).toString("hex"),
  VIEWER_OWNER_SCOPE: crypto.randomBytes(12).toString("hex"),
  PRIVATE_SERVICE_TOKEN_SHA256: sha256(serviceToken),
  INGEST_TOKEN: crypto.randomBytes(32).toString("hex"),
};

const moduleCache = new Map();
function loadTypeScript(modulePath) {
  const absolute = path.resolve(modulePath);
  if (moduleCache.has(absolute)) return moduleCache.get(absolute).exports;
  const moduleObject = { exports: {} };
  moduleCache.set(absolute, moduleObject);
  const source = fs.readFileSync(absolute, "utf8");
  const compiled = typescript.transpileModule(source, {
    compilerOptions: {
      target: typescript.ScriptTarget.ES2022,
      module: typescript.ModuleKind.CommonJS,
      esModuleInterop: true,
    },
  }).outputText;
  const resolveModule = specifier => {
    if (specifier === "cloudflare:workers") return { env };
    if (specifier.startsWith("node:")) return require(specifier);
    if (!specifier.startsWith(".")) throw new Error("UNAPPROVED_IMPORT");
    const base = path.resolve(path.dirname(absolute), specifier);
    for (const candidate of [base, base + ".ts", base.replace(/\.js$/, ".ts"), path.join(base, "index.ts")]) {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return loadTypeScript(candidate);
    }
    throw new Error("MODULE_MISSING");
  };
  const globals = {
    module: moduleObject,
    exports: moduleObject.exports,
    require: resolveModule,
    Request, Response, Headers, URL, URLSearchParams,
    TextEncoder, TextDecoder, ReadableStream, AbortController, AbortSignal,
    Uint8Array, Buffer, crypto: crypto.webcrypto,
    Date, setTimeout, clearTimeout, setInterval, clearInterval,
    queueMicrotask, console, atob, btoa,
  };
  vm.runInNewContext(compiled, globals, { filename: absolute });
  return moduleObject.exports;
}

const gateway = loadTypeScript(path.join(dashboardRoot, "lib/viewer-gateway.ts"));
const routePaths = [
  "/api/state", "/api/orders", "/api/pnl", "/api/ingest", "/api/observability/stream",
];
const routes = new Map(routePaths.map(routePath => {
  const relative = routePath.slice("/api/".length).split("/");
  const routeFile = path.join(dashboardRoot, "app/api", ...relative, "route.ts");
  return [routePath, loadTypeScript(routeFile)];
}));
let publicDispatches = 0;
let privateIngestDispatches = 0;
let privateIngestSqlWrites = 0;
let privateIngestSqlReads = 0;
const dispatch = async (request, capability) => {
  const mode = capability?.kind === "private" ? "private_ingest" : "public";
  if (mode === "private_ingest") privateIngestDispatches++;
  else publicDispatches++;
  const url = new URL(request.url);
  const route = routes.get(url.pathname);
  const handler = route && route[request.method];
  return requestMode.run(mode, async () => {
    if (!handler) return new Response(null, { status: 404 });
    return handler(request);
  });
};

const now = Date.now();
const snapshot = {
  schema_version: 1,
  generated_ms: now,
  markets: [], orders: [], strategies: [], timefolio: [],
  observability: {
    pnl: [], latency: [], latency_status: "NOT_CONFIGURED",
    native_status: "NOT_CONFIGURED", etf_status: "NOT_CONFIGURED",
  },
};
sqlite.prepare("INSERT INTO snapshots VALUES ('server', ?, ?, ?)")
  .run(now, now, JSON.stringify(snapshot));

const syntheticTelemetrySnapshot = {
  schema_version: 1,
  generated_ms: Date.now(),
  markets: [{ key: "synthetic:BTCUSDT", venue: "fixture", symbol: "BTCUSDT",
    section: "crypto", asset_class: "crypto_perp", currency: "USD", price: "100" }],
  orders: [], strategies: [], timefolio: [],
  observability: {
    pnl: [], latency: [], latency_status: "NOT_CONFIGURED", latency_error: null,
    native_status: "NOT_CONFIGURED", native_error: null,
    etf_status: "NOT_CONFIGURED", etf_error: null,
    end_to_end_ms: null, end_to_end_status: "SEGMENTED",
  },
};
let telemetrySourceType = args.telemetryInput ? "input-json" : "synthetic-fixture";
function boundedTelemetrySnapshot() {
  let value = syntheticTelemetrySnapshot;
  if (args.telemetryInput) {
    let content;
    try {
      const stat = fs.statSync(args.telemetryInput);
      if (!stat.isFile() || stat.size > 1024 * 1024) throw new Error("TELEMETRY_INPUT_TOO_LARGE");
      content = fs.readFileSync(args.telemetryInput, "utf8");
    } catch (error) {
      if (error?.message === "TELEMETRY_INPUT_TOO_LARGE") throw error;
      throw new Error("TELEMETRY_INPUT_UNAVAILABLE");
    }
    try { value = JSON.parse(content); } catch { throw new Error("TELEMETRY_INPUT_INVALID"); }
  }
  if (!value || typeof value !== "object" || Array.isArray(value) || value.schema_version !== 1 ||
      !Array.isArray(value.markets) || value.markets.length > 512 ||
      !Array.isArray(value.orders) || value.orders.length > 1000 ||
      !Array.isArray(value.strategies) || value.strategies.length > 100 ||
      !Array.isArray(value.timefolio) || value.timefolio.length > 50 ||
      (Array.isArray(value.sources) && value.sources.length > 256) ||
      JSON.stringify(value).length > 1024 * 1024) throw new Error("TELEMETRY_INPUT_INVALID");
  const result = { ...value, generated_ms: Date.now() };
  if (!result.observability || typeof result.observability !== "object") {
    throw new Error("TELEMETRY_INPUT_INVALID");
  }
  return result;
}

const sourceHashes = {};
for (const relative of requiredFiles) sourceHashes[`dashboard/${relative}`] = hashFile(path.join(dashboardRoot, relative));
sourceHashes["perf01_viewers.cjs"] = hashFile(__filename);
sourceHashes["typescript.js"] = hashFile(args.typescript);
sourceHashes["tls_certificate"] = hashFile(args.cert);

const report = {
  result: "RUNNING",
  ssl_status: "LOCAL_TEST_ONLY",
  external_network: false,
  source_root: args.sourceRoot,
  dashboard_root: dashboardRoot,
  source_hashes: sourceHashes,
  host: "127.0.0.1",
  tls_server_name: "dashboard.example.test",
  viewer_count: 2,
  requested_interval_ms: args.intervalMs,
  requested_duration_seconds: args.durationSeconds,
  method: "two authenticated HTTPS GET /api/state clients; each request opens a fresh TLS connection",
  disconnect: args.disconnectSeconds === null ? { enabled: false } : {
    enabled: true,
    clock_domain: `node-hrtime:${process.pid}`,
    configured_after_seconds: args.disconnectAfterSeconds,
    configured_duration_seconds: args.disconnectSeconds,
    actual_pause_ms: null,
  },
  public_application_sql_writes: 0,
  public_dispatch_count: 0,
  private_ingest_dispatch_count: 0,
  private_ingest: { source: args.telemetryInput ? "input-json" : "synthetic-fixture",
    ticks: 0, successes: 0, errors: [], request_bytes: 0, durations_ms: [], intervals_ms: [] },
  sqlite: { migration_applied: true, reads: 0, writes: 0 },
  viewers: [],
  open_client_requests_at_stop: null,
  open_server_requests_at_stop: null,
};

let server;
let port;
let stopping = false;
let durationTimer;
let stopResolve;
let benchmarkStopAt = null;
let readyResolver;
let readinessEmitted = false;
let readinessGeneratedMs = null;
let disconnectState = args.disconnectSeconds === null ? "disabled" : "scheduled";
let disconnectTimer;
let disconnectStartedAtMs = null;
let disconnectStartedMono = null;
let disconnectEndedAtMs = null;
let disconnectEndedMono = null;
let disconnectRequestCountsAtStart = null;
let disconnectInflightAtStart = null;
let activePublicPolls = 0;
let disconnectGateResolve;
const disconnectGate = new Promise(resolve => { disconnectGateResolve = resolve; });
const stopped = new Promise(resolve => { stopResolve = resolve; });
const viewersReady = new Promise(resolve => { readyResolver = resolve; });
let openClientRequests = 0;
let openServerRequests = 0;
const activeClientRequests = new Set();
const stop = reason => {
  if (stopping) return;
  stopping = true;
  report.stop_reason = reason;
  if (durationTimer) clearTimeout(durationTimer);
  if (disconnectTimer && disconnectState === "scheduled") clearTimeout(disconnectTimer);
  if (disconnectState === "scheduled" || disconnectState === "pending" || disconnectState === "paused") {
    disconnectState = "stopped";
    disconnectGateResolve();
  }
  stopResolve();
};
process.on("SIGTERM", () => stop("SIGTERM"));
process.on("SIGINT", () => stop("SIGINT"));

const waitUntilOrStop = async deadline => {
  const delay = Math.max(0, deadline - Date.now());
  if (delay === 0 || stopping) return;
  await new Promise(resolve => {
    let timer;
    const done = () => { if (timer) clearTimeout(timer); resolve(); };
    timer = setTimeout(done, delay);
    stopped.then(done);
  });
};

async function handleIncoming(request, response) {
  openServerRequests++;
  let closed = false;
  const finish = () => {
    if (closed) return;
    closed = true;
    openServerRequests--;
  };
  response.on("finish", finish);
  response.on("close", finish);
  const abort = new AbortController();
  response.on("close", () => abort.abort());
  try {
    const chunks = [];
    let size = 0;
    if (request.method !== "GET" && request.method !== "HEAD") {
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 2048) {
          response.writeHead(413).end();
          return;
        }
        chunks.push(chunk);
      }
    }
    const body = chunks.length ? Buffer.concat(chunks) : undefined;
    const init = {
      method: request.method,
      headers: request.headers,
      signal: abort.signal,
      ...(body ? { body, duplex: "half" } : {}),
    };
    const webRequest = new Request(`https://${request.headers.host}${request.url}`, init);
    const result = await gateway.publicFetch(webRequest, env, dispatch);
    const headers = Object.fromEntries(result.headers);
    response.writeHead(result.status, headers);
    if (result.body) Readable.fromWeb(result.body).pipe(response);
    else response.end();
  } catch {
    if (!response.headersSent) response.writeHead(500);
    response.end();
  }
}

function requestTls(pathname, { method = "GET", headers = {}, body = undefined } = {}) {
  return new Promise((resolve, reject) => {
    const started = process.hrtime.bigint();
    const request = https.request({
      host: "127.0.0.1",
      port,
      servername: "dashboard.example.test",
      ca: fs.readFileSync(args.cert),
      agent: false,
      method,
      path: pathname,
      headers: {
        host: `dashboard.example.test:${port}`,
        ...headers,
        ...(body ? { "content-length": Buffer.byteLength(body) } : {}),
      },
    }, response => {
      const chunks = [];
      let byteCount = 0;
      response.on("data", chunk => { byteCount += chunk.length; chunks.push(chunk); });
      response.on("end", () => {
        openClientRequests--;
        activeClientRequests.delete(request);
        resolve({
          status: response.statusCode || 0,
          headers: response.headers,
          body: Buffer.concat(chunks),
          bytes: byteCount,
          elapsedMs: Number(process.hrtime.bigint() - started) / 1e6,
        });
      });
    });
    openClientRequests++;
    activeClientRequests.add(request);
    request.setTimeout(8000, () => request.destroy(Object.assign(new Error("timeout"), { code: "ETIMEDOUT" })));
    request.on("error", error => {
      openClientRequests--;
      activeClientRequests.delete(request);
      reject(error);
    });
    if (body) request.write(body);
    request.end();
  });
}

const sleepReal = ms => new Promise(resolve => setTimeout(resolve, ms));
async function login() {
  const response = await requestTls("/viewer/session", {
    method: "POST",
    headers: {
      origin: env.VIEWER_ORIGIN,
      "content-type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({ token }).toString(),
  });
  if (response.status !== 303) throw new Error("LOGIN_REJECTED");
  const setCookie = response.headers["set-cookie"];
  const first = Array.isArray(setCookie) ? setCookie[0] : setCookie;
  const cookie = typeof first === "string" ? first.split(";", 1)[0] : "";
  if (!cookie.startsWith("__Host-vt_viewer=") || !cookie.includes(".")) throw new Error("COOKIE_MISSING");
  return cookie;
}

const percentile = (values, p) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(p * sorted.length) - 1)];
};
const summarize = values => ({
  n: values.length,
  p50_ms: percentile(values, 0.50),
  p95_ms: percentile(values, 0.95),
  p99_ms: percentile(values, 0.99),
  max_ms: values.length ? Math.max(...values) : null,
});

async function pollViewer(viewer, onFirstSuccess) {
  let nextAt = Date.now();
  while (!stopping) {
    await waitUntilOrStop(nextAt);
    if (stopping || (benchmarkStopAt !== null && Date.now() >= benchmarkStopAt)) break;
    if (disconnectState === "pending" || disconnectState === "paused") {
      viewer.pauseWaits++;
      await disconnectGate;
      if (stopping || disconnectState === "stopped") break;
      if (disconnectEndedAtMs !== null) {
        viewer.pausedPolls += Math.max(0,
          Math.floor((disconnectEndedAtMs - nextAt) / args.intervalMs));
      }
    }
    const started = Date.now();
    const startedMono = process.hrtime.bigint();
    if (viewer.pollStartsNs.length) {
      const intervalMs = Number(startedMono - viewer.pollStartsNs[viewer.pollStartsNs.length - 1]) / 1e6;
      viewer.intervalsMs.push(intervalMs);
      const phase = disconnectState === "complete" ? "after" : "before";
      if (viewer.pollPhases[viewer.pollPhases.length - 1] === phase) {
        viewer.activeIntervalsMs[phase].push(intervalMs);
      }
    }
    viewer.pollStartsNs.push(startedMono);
    viewer.pollOffsetsMs.push(Number(startedMono - viewer.runStartedMono) / 1e6);
    viewer.pollPhases.push(disconnectState === "complete" ? "after" : "before");
    activePublicPolls++;
    try {
      const response = await requestTls("/api/state", { headers: { cookie: viewer.cookie } });
      viewer.statuses[String(response.status)] = (viewer.statuses[String(response.status)] || 0) + 1;
      viewer.requests++;
      viewer.bytesReceived += response.bytes;
      viewer.durationsMs.push(response.elapsedMs);
      if (response.status !== 200) {
        viewer.errors.push("HTTP_STATUS");
      } else {
        try {
          const state = JSON.parse(response.body.toString("utf8"));
          if (state.read_only !== true || state.schema_version !== 1) viewer.errors.push("INVALID_STATE");
          else if (readinessGeneratedMs !== null && Number.isSafeInteger(state.generated_ms) &&
                   state.generated_ms >= readinessGeneratedMs && !viewer.ready) {
            viewer.ready = true;
            onFirstSuccess(viewer);
          }
          if (state.read_only === true && disconnectState === "complete" && !viewer.recovered) {
            viewer.recovered = true;
            viewer.recoveryAtMs = Date.now();
            viewer.recoveryLatencyMs = Number(process.hrtime.bigint() - disconnectEndedMono) / 1e6;
            viewer.recoverySnapshotGeneratedMs = state.generated_ms;
          }
        } catch {
          viewer.errors.push("INVALID_STATE");
        }
      }
      // Every request deliberately uses agent:false, so the next poll verifies
      // a fresh TLS handshake and exercises a real local reconnect.
      if (viewer.requests > 1) viewer.reconnects++;
    } catch (error) {
      viewer.errors.push(error && error.code === "ETIMEDOUT" ? "TLS_TIMEOUT" : "TLS_REQUEST_ERROR");
      viewer.failedRequests++;
    }
    activePublicPolls--;
    if (benchmarkStopAt !== null && Date.now() >= benchmarkStopAt) break;
    nextAt = started + args.intervalMs;
  }
}

async function executeDisconnect(viewers) {
  if (disconnectState !== "scheduled") return;
  disconnectState = "pending";
  while (activePublicPolls > 0 && !stopping) await waitUntilOrStop(Date.now() + 10);
  if (stopping) {
    disconnectState = "stopped";
    disconnectGateResolve();
    return;
  }
  disconnectRequestCountsAtStart = viewers.map(viewer => viewer.requests);
  disconnectInflightAtStart = activePublicPolls;
  disconnectStartedAtMs = Date.now();
  disconnectStartedMono = process.hrtime.bigint();
  disconnectState = "paused";
  await waitUntilOrStop(disconnectStartedAtMs + args.disconnectSeconds * 1000);
  if (!stopping) {
    disconnectEndedAtMs = Date.now();
    disconnectEndedMono = process.hrtime.bigint();
    disconnectState = "complete";
  } else {
    disconnectState = "stopped";
  }
  disconnectGateResolve();
}

async function privateIngestLoop(onFirstSuccess) {
  const privateFetch = gateway.privateFetch;
  let nextAt = Date.now();
  let previousStart = null;
  const metrics = {
    ticks: 0, successes: 0, statuses: {}, errors: [], requestBytes: 0,
    durationsMs: [], intervalsMs: [], startsNs: [],
  };
  while (!stopping) {
    await waitUntilOrStop(nextAt);
    if (stopping || (benchmarkStopAt !== null && Date.now() >= benchmarkStopAt)) break;
    const started = Date.now();
    const startedNs = process.hrtime.bigint();
    if (previousStart !== null) metrics.intervalsMs.push(Number(startedNs - previousStart) / 1e6);
    previousStart = startedNs;
    metrics.startsNs.push(startedNs);
    metrics.ticks++;
    try {
      const payload = boundedTelemetrySnapshot();
      const body = JSON.stringify(payload);
      const request = new Request("https://private-binding.local/api/ingest", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "content-length": String(Buffer.byteLength(body)),
          authorization: `Bearer ${env.INGEST_TOKEN}`,
          "x-private-service-authorization": `Bearer ${serviceToken}`,
        },
        body,
        duplex: "half",
      });
      metrics.requestBytes += Buffer.byteLength(body);
      const begin = process.hrtime.bigint();
      const response = await privateFetch(request, env, dispatch);
      metrics.durationsMs.push(Number(process.hrtime.bigint() - begin) / 1e6);
      metrics.statuses[String(response.status)] = (metrics.statuses[String(response.status)] || 0) + 1;
      if (response.body) await response.body.cancel();
      if (response.status === 200) {
        metrics.successes++;
        if (readinessGeneratedMs === null) readinessGeneratedMs = payload.generated_ms;
        onFirstSuccess(payload.generated_ms);
      } else {
        metrics.errors.push("PRIVATE_INGEST_HTTP_STATUS");
      }
    } catch (error) {
      const message = error && error.message;
      metrics.errors.push(["TELEMETRY_INPUT_TOO_LARGE", "TELEMETRY_INPUT_UNAVAILABLE",
        "TELEMETRY_INPUT_INVALID"].includes(message) ? message : "PRIVATE_INGEST_ERROR");
    }
    if (benchmarkStopAt !== null && Date.now() >= benchmarkStopAt) break;
    nextAt = started + args.intervalMs;
  }
  return metrics;
}

async function main() {
  server = https.createServer({
    key: fs.readFileSync(args.key),
    cert: fs.readFileSync(args.cert),
  }, handleIncoming);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  port = server.address().port;
  env.VIEWER_ORIGIN = `https://dashboard.example.test:${port}`;

  const firstCookie = await login();
  // Ensure unique signed cookie claims so these are two distinct local sessions.
  const firstIssuedSecond = Math.floor(Date.now() / 1000);
  while (Math.floor(Date.now() / 1000) === firstIssuedSecond) await sleepReal(10);
  const secondCookie = await login();
  if (firstCookie === secondCookie) throw new Error("SESSIONS_NOT_DISTINCT");
  report.independent_cookie_sessions = true;
  const viewers = [firstCookie, secondCookie].map((cookie, index) => ({
    id: `viewer-${index + 1}`,
    cookie,
    requests: 0,
    failedRequests: 0,
    reconnects: 0,
    bytesReceived: 0,
    statuses: {},
    errors: [],
    durationsMs: [],
    pollStartsNs: [],
    pollOffsetsMs: [],
    intervalsMs: [],
    activeIntervalsMs: { before: [], after: [] },
    pollPhases: [],
    ready: false,
    pauseWaits: 0,
    pausedPolls: 0,
    recovered: false,
    recoveryAtMs: null,
    recoveryLatencyMs: null,
    recoverySnapshotGeneratedMs: null,
  }));
  const startedAt = Date.now();
  const startedMono = process.hrtime.bigint();
  for (const viewer of viewers) viewer.runStartedMono = startedMono;
  let telemetryReady = false;
  const markFirstSuccess = () => {
    if (readinessEmitted || !telemetryReady || !viewers.every(viewer => viewer.ready)) return;
    readinessEmitted = true;
    benchmarkStopAt = Date.now() + args.durationSeconds * 1000;
    durationTimer = setTimeout(() => stop("DURATION_COMPLETE"), args.durationSeconds * 1000);
    if (args.disconnectSeconds !== null) {
      disconnectTimer = setTimeout(() => { void executeDisconnect(viewers); },
        args.disconnectAfterSeconds * 1000);
    }
    console.log(JSON.stringify({
      event: "READY", pid: process.pid, host: "127.0.0.1", port,
      ready_viewers: 2, ssl_status: "LOCAL_TEST_ONLY",
    }));
    readyResolver();
  };
  const loops = viewers.map(viewer => pollViewer(viewer, markFirstSuccess));
  const ingestion = privateIngestLoop(() => {
    telemetryReady = true;
    markFirstSuccess();
  });
  const startupTimer = setTimeout(() => stop("STARTUP_TIMEOUT"), 15_000);
  await Promise.race([viewersReady, stopped]);
  clearTimeout(startupTimer);
  if (!readinessEmitted) {
    report.error = "VIEWER_STARTUP_TIMEOUT";
    await Promise.all(loops);
  }
  await Promise.race([Promise.all(loops), stopped]);
  stop(stopping ? report.stop_reason : "DURATION_COMPLETE");
  await Promise.all(loops);
  const ingestMetrics = await ingestion;
  durationTimer && clearTimeout(durationTimer);
  report.started_at_ms = startedAt;
  report.ended_at_ms = Date.now();
  report.actual_duration_ms = Number(process.hrtime.bigint() - startedMono) / 1e6;
  report.pid = process.pid;
  report.port = port;
  report.public_dispatch_count = publicDispatches;
  report.private_ingest_dispatch_count = privateIngestDispatches;
  report.public_application_sql_writes = sqlByMode.public.writes;
  privateIngestSqlWrites = sqlByMode.private_ingest.writes;
  privateIngestSqlReads = sqlByMode.private_ingest.reads;
  report.private_ingest = {
    source: telemetrySourceType,
    ticks: ingestMetrics.ticks,
    successes: ingestMetrics.successes,
    statuses: ingestMetrics.statuses,
    errors: [...new Set(ingestMetrics.errors)],
    durations_ms: ingestMetrics.durationsMs,
    duration_summary: summarize(ingestMetrics.durationsMs),
    intervals_ms: ingestMetrics.intervalsMs,
    interval_summary: summarize(ingestMetrics.intervalsMs),
    request_bytes: ingestMetrics.requestBytes,
    sql_writes: privateIngestSqlWrites,
    sql_reads: privateIngestSqlReads,
  };
  report.sqlite = { migration_applied: true, reads: sqlReads, writes: sqlWrites,
    public_reads: sqlByMode.public.reads, public_writes: sqlByMode.public.writes,
    private_ingest_reads: sqlByMode.private_ingest.reads,
    private_ingest_writes: sqlByMode.private_ingest.writes };
  report.clock_domain = `node-hrtime:${process.pid}`;
  report.database_storage = "IN_MEMORY_SQLITE";
  report.disconnect = args.disconnectSeconds === null ? { enabled: false } : {
    enabled: true,
    clock_domain: `node-hrtime:${process.pid}`,
    configured_after_seconds: args.disconnectAfterSeconds,
    configured_duration_seconds: args.disconnectSeconds,
    state: disconnectState,
    started_at_ms: disconnectStartedAtMs,
    ended_at_ms: disconnectEndedAtMs,
    start_offset_ms: disconnectStartedMono === null ? null :
      Number(disconnectStartedMono - startedMono) / 1e6,
    actual_pause_ms: disconnectStartedMono !== null && disconnectEndedMono !== null ?
      Number(disconnectEndedMono - disconnectStartedMono) / 1e6 : null,
    active_ms_before: disconnectStartedMono === null ? null : Number(disconnectStartedMono - startedMono) / 1e6,
    active_ms_after: disconnectEndedMono === null ? null :
      Number(process.hrtime.bigint() - disconnectEndedMono) / 1e6,
    requests_at_pause_start: disconnectRequestCountsAtStart,
    public_requests_in_flight_at_pause_start: disconnectInflightAtStart,
    requests_after_recovery: disconnectRequestCountsAtStart === null ? null :
      viewers.map((viewer, index) => viewer.requests - disconnectRequestCountsAtStart[index]),
    scheduled_polls_paused: viewers.map(viewer => viewer.pausedPolls),
    recovered_viewers: viewers.filter(viewer => viewer.recovered).length,
    recovery_latency_ms: viewers.map(viewer => viewer.recoveryLatencyMs),
    recovery_snapshot_generated_ms: viewers.map(viewer => viewer.recoverySnapshotGeneratedMs),
    active_intervals_ms: viewers.map(viewer => ({
      before: summarize(viewer.activeIntervalsMs.before),
      after: summarize(viewer.activeIntervalsMs.after),
    })),
  };
  report.viewers = viewers.map(({
    cookie: _cookie, runStartedMono: _runStartedMono,
    pollStartsNs: _pollStartsNs, pollOffsetsMs, pollPhases: _pollPhases,
    activeIntervalsMs, ready: _ready, ...viewer
  }) => ({
    ...viewer,
    poll_offsets_ms: pollOffsetsMs,
    request_duration: summarize(viewer.durationsMs),
    interval_actual: summarize(viewer.intervalsMs),
    active_interval_before_disconnect: summarize(activeIntervalsMs.before),
    active_interval_after_disconnect: summarize(activeIntervalsMs.after),
    error_count: viewer.errors.length,
    errors: [...new Set(viewer.errors)],
    tls_connections: viewer.requests + viewer.failedRequests,
    sse_connections: 0,
  }));
  delete report.requested_duration_seconds;
  report.duration_seconds_requested = args.durationSeconds;
  report.open_client_requests_at_stop = openClientRequests;
  report.open_server_requests_at_stop = openServerRequests;
  report.application_state = "LOCAL_SCRIPTED_SQLITE_ONLY";
  report.session_and_service_credentials_emitted = false;
  report.ready_viewers = readinessEmitted ? viewers.filter(viewer => viewer.ready).length : 0;
  report.readiness_snapshot_generated_ms = readinessGeneratedMs;
  const success = viewers.length === 2 && readinessEmitted && report.viewers.every(viewer =>
    viewer.failedRequests === 0 && viewer.statuses["200"] === viewer.requests &&
    viewer.requests > 0 && viewer.error_count === 0) &&
    (args.disconnectSeconds === null || (disconnectState === "complete" &&
      report.disconnect.actual_pause_ms >= args.disconnectSeconds * 1000 &&
      report.disconnect.recovered_viewers === 2)) &&
    ingestMetrics.successes > 0 && ingestMetrics.errors.length === 0 &&
    report.public_application_sql_writes === 0 &&
    privateIngestSqlWrites > 0 && sqlWrites === privateIngestSqlWrites &&
    openClientRequests === 0 && openServerRequests === 0 ? "PASS" : "FAIL";
  if (report.result !== "FAIL") report.result = success;
}

function sanitizedFatal(error) {
  const code = error && error.message;
  if (["ARGUMENT", "SOURCE_MISSING", "INPUT_MISSING", "LOGIN_REJECTED",
       "COOKIE_MISSING", "SESSIONS_NOT_DISTINCT", "EADDRINUSE"].includes(code)) return code;
  return "INITIALIZATION_OR_RUNTIME_ERROR";
}

main().catch(error => {
  report.result = "FAIL";
  report.error = sanitizedFatal(error);
}).finally(async () => {
  stop(report.stop_reason || "FINALIZE");
  for (const request of activeClientRequests) request.destroy();
  if (server) {
    server.closeAllConnections();
    await new Promise(resolve => server.close(() => resolve()));
  }
  report.open_client_requests_at_stop = openClientRequests;
  report.open_server_requests_at_stop = openServerRequests;
  report.sqlite = { migration_applied: true, reads: sqlReads, writes: sqlWrites,
    public_reads: sqlByMode.public.reads, public_writes: sqlByMode.public.writes,
    private_ingest_reads: sqlByMode.private_ingest.reads,
    private_ingest_writes: sqlByMode.private_ingest.writes };
  try { sqlite.close(); } catch { report.result = "FAIL"; report.error = "SQLITE_CLOSE_ERROR"; }
  fs.mkdirSync(path.dirname(args.output), { recursive: true });
  fs.writeFileSync(args.output, JSON.stringify(report, null, 2), { mode: 0o600 });
  if (report.result === "FAIL") process.exitCode = 1;
  console.log(JSON.stringify({ event: "FINAL", result: report.result,
    requests: report.viewers.map(viewer => viewer.requests),
    open_client_requests: report.open_client_requests_at_stop,
    open_server_requests: report.open_server_requests_at_stop }));
});
