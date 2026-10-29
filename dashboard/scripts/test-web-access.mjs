import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { register } from "node:module";
import { publicSnapshot, publicPnl, publicOrders, publicScope } from "../lib/public-projection.ts";
import { sealOrderCursor, openOrderCursor } from "../lib/order-cursor.ts";

register(`data:text/javascript,${encodeURIComponent(`
export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith(".") && !/\\.(?:mjs|js|ts|json)$/.test(specifier)) {
    try { return await nextResolve(specifier + ".ts", context); } catch {}
  }
  return nextResolve(specifier, context);
}`)}`, import.meta.url);
const { ViewerGateway, publicFetch, privateFetch } = await import("../lib/viewer-gateway.ts");

const token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
const privateToken = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789";
const digest = value => createHash("sha256").update(value).digest("hex");
const owner = "0123456789abcdef01234567";
const base = { VIEWER_ORIGIN: "https://dashboard.example", VIEWER_TOKEN_SHA256: digest(token),
  VIEWER_SESSION_SECRET: "independent-session-secret-with-32-plus-characters", VIEWER_OWNER_SCOPE: owner,
  PRIVATE_SERVICE_TOKEN_SHA256: digest(privateToken) };
const headers = (more = {}) => ({ "cf-connecting-ip": "192.0.2.1", ...more });
const request = (path, { method = "GET", cookie, origin, headers: extra = {}, body } = {}) => new Request(base.VIEWER_ORIGIN + path, {
  method, headers: headers({ ...(cookie ? { cookie } : {}), ...(origin ? { origin } : {}), ...extra }), body,
});
async function login(gateway, ip = "192.0.2.1", secret = token) {
  const req = new Request(base.VIEWER_ORIGIN + "/viewer/session", { method: "POST", headers: headers({ origin: base.VIEWER_ORIGIN,
    "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": ip }), body: new URLSearchParams({ token: secret }) });
  return gateway.fetch(req, async () => new Response("unexpected"));
}
const cookieFrom = response => response.headers.get("set-cookie")?.split(";")[0];
async function signedCookie(gateway) { const response = await login(gateway); assert.equal(response.status, 303); return cookieFrom(response); }

test("config fails closed and untrusted oai identity headers never authenticate", async () => {
  const missing = new ViewerGateway({});
  assert.equal((await missing.fetch(request("/"), async () => new Response("called"))).status, 503);
  const gateway = new ViewerGateway(base);
  let called = false;
  const response = await gateway.fetch(request("/api/state", { headers: { "oai-authenticated-user-id": "attacker", "x-forwarded-user": "forged" }, origin: base.VIEWER_ORIGIN }), async () => { called = true; return new Response("bad"); });
  assert.equal(response.status, 401); assert.equal(called, false);
});

test("login issues protected cookie; host and origin are exact", async () => {
  const gateway = new ViewerGateway(base), response = await login(gateway);
  assert.equal(response.status, 303);
  const cookie = response.headers.get("set-cookie");
  assert.match(cookie, /^__Host-vt_viewer=/); assert.match(cookie, /Secure/); assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Strict/); assert.match(cookie, /Path=\//); assert.match(cookie, /Max-Age=600/);
  assert.ok(!cookie.includes(token));
  const plaintext = new Request("http://dashboard.example/viewer/session", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ token }) });
  const redirect = await gateway.fetch(plaintext, async () => new Response("called"));
  assert.equal(redirect.status, 308); assert.equal(redirect.headers.get("location"), "https://dashboard.example/viewer/session");
  const wrongHost = new Request("https://attacker.example/api/state", { headers: headers({ cookie: cookieFrom(response) }) });
  assert.equal((await gateway.fetch(wrongHost, async () => new Response("bad"))).status, 403);
  assert.equal((await gateway.fetch(request("/api/state", { cookie: cookieFrom(response), origin: "https://evil.example" }), async () => new Response())).status, 403);
});

test("public root redirects unauthenticated viewers; allowed projection key survives query guard", async () => {
  const gateway = new ViewerGateway(base), root = await gateway.fetch(request("/"), async () => new Response("called"));
  assert.equal(root.status, 303); assert.equal(root.headers.get("location"), "/viewer/login");
  const cookie = await signedCookie(gateway), key = "a".repeat(64);
  const response = await gateway.fetch(request(`/api/pnl?key=${key}`, { cookie }), async () => new Response("ok"));
  assert.equal(response.status, 200);
  assert.equal((await gateway.fetch(request("/api/pnl?key=credential", { cookie }), async () => new Response("called"))).status, 403);
  assert.equal((await gateway.fetch(request("/api/orders?section=crypto&cursor=opaque", { cookie }), async () => new Response("ok"))).status, 200);
  assert.equal((await gateway.fetch(request("/api/orders?section=crypto&before_id=raw", { cookie }), async () => new Response("called"))).status, 403);
});

test("session credential exchange is bounded and rejects invalid token/body/origin", async () => {
  const gateway = new ViewerGateway(base);
  const sharedCredential = new ViewerGateway({ ...base, PRIVATE_SERVICE_TOKEN_SHA256: base.VIEWER_TOKEN_SHA256 });
  assert.equal((await sharedCredential.fetch(request("/"), async () => new Response("called"))).status, 503);
  assert.equal((await login(gateway, "192.0.2.1", "wrong-wrong-wrong-wrong-wrong-wrong")).status, 401);
  const wrongOrigin = new Request(base.VIEWER_ORIGIN + "/viewer/session", { method: "POST", headers: headers({ origin: "https://evil.example", "content-type": "application/x-www-form-urlencoded" }), body: new URLSearchParams({ token }) });
  assert.equal((await gateway.fetch(wrongOrigin, async () => new Response())).status, 403);
  const oversized = new Request(base.VIEWER_ORIGIN + "/viewer/session", { method: "POST", headers: headers({ origin: base.VIEWER_ORIGIN, "content-type": "application/x-www-form-urlencoded" }), body: "x=" + "x".repeat(1100) });
  assert.equal((await gateway.fetch(oversized, async () => new Response())).status, 400);
  const rateLimited = new ViewerGateway(base);
  for (let i = 0; i < 5; i++) await login(rateLimited, "192.0.2.44", "wrong-wrong-wrong-wrong-wrong-wrong");
  assert.equal((await login(rateLimited, "192.0.2.44", token)).status, 429);
  for (let i = 0; i < 1050; i++) await rateLimited.fetch(request("/not-found", { headers: { "cf-connecting-ip": `198.51.100.${i}` } }), async () => new Response());
  assert.ok(rateLimited.rates.size <= 1024);
});

test("expired, altered and credential-rotated sessions are rejected", async () => {
  const gateway = new ViewerGateway(base), cookie = await signedCookie(gateway), initial = Date.now;
  try {
    Date.now = () => initial() + 601_000;
    assert.equal((await gateway.fetch(request("/api/state", { cookie }), async () => new Response())).status, 401);
  } finally { Date.now = initial; }
  const [cookiePayload, cookieSignature] = cookie.split(".");
  const signatureBytes = Buffer.from(cookieSignature, "base64url");
  signatureBytes[signatureBytes.length - 1] ^= 1;
  const altered = cookiePayload + "." + signatureBytes.toString("base64url");
  assert.equal((await gateway.fetch(request("/api/state", { cookie: altered }), async () => new Response())).status, 401);
  const rotated = new ViewerGateway({ ...base, VIEWER_TOKEN_SHA256: digest(token + "rotated") });
  assert.equal((await rotated.fetch(request("/api/state", { cookie }), async () => new Response())).status, 401);
});

test("viewer methods and routes are an allowlist; private admin is separate", async () => {
  const gateway = new ViewerGateway(base), cookie = await signedCookie(gateway);
  for (const path of ["/api/ingest", "/api/accounts", "/api/orders/submit", "/api/state/extra"]) {
    const response = await gateway.fetch(request(path, { method: path === "/api/state/extra" ? "GET" : "POST", cookie, origin: base.VIEWER_ORIGIN }), async () => new Response("called"));
    assert.equal(response.status, 403, path);
  }
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]) {
    assert.equal((await gateway.fetch(request("/api/state", { method, cookie, origin: base.VIEWER_ORIGIN }), async () => new Response())).status, 403, method);
  }
  const trace = { url: base.VIEWER_ORIGIN + "/api/state", method: "TRACE", headers: new Headers({ cookie }), body: null };
  assert.equal((await gateway.fetch(trace, async () => new Response())).status, 403);
  let dispatches = 0;
  const valid = await gateway.fetch(request("/api/state", { cookie }), async (_req, cap) => { dispatches++; assert.equal(cap.owner, owner); assert.equal(cap.kind, "viewer"); return Response.json({ ok: true }, { headers: { "set-cookie": "framework=secret" } }); });
  assert.equal(valid.status, 200); assert.equal(valid.headers.has("set-cookie"), false); assert.equal(dispatches, 1);
  const privateReq = new Request("https://private-binding/api/ingest", { method: "POST", headers: { "x-private-service-authorization": `Bearer ${privateToken}`, authorization: "Bearer ingest-secret" } });
  const privateResult = await privateFetch(privateReq, base, async (_req, cap) => { dispatches++; assert.equal(cap.kind, "private"); return new Response("private"); });
  assert.equal(privateResult.status, 200); assert.equal(dispatches, 2);
  const denied = await privateFetch(new Request("https://private-binding/api/ingest", { method: "POST" }), base, async () => new Response("called"));
  assert.equal(denied.status, 401);
});

test("public projections remove credential, owner, raw identifier and error canaries", () => {
  const canary = "SECRET-CANARY-DO-NOT-RETURN";
  const state = publicSnapshot({ generated_ms: 10, public_key: { key: canary }, errors: [canary],
    accounts: [{ id: canary, envelope: canary }], strategies: [{ id: "strategy", account: "raw-account", status: "running", private: canary }],
    orders: [{ id: canary, account: "raw-account", symbol: "BTC", price: { nested: canary }, secret: canary }],
    markets: [{ key: "BTC", gap_reason: canary, price: canary, book: { bids: [["10", "2"], [canary, "1"], [{ secret: canary }, "1"]], asks: [] } }],
    timefolio: [{ id: "account-one", label: canary, positions: [{ ticker: "BTC", qty: 1, weight_pct: 10, credential: canary }], envelope: canary }, { id: "account-two", label: canary, positions: [] }],
    observability: { pnl: [{ key: "a".repeat(64), account_scope: "raw-account", strategy_id: "strategy", equity_curve: [{ ts: 1, net_pnl: "1", secret: canary }] }], latency: [{ process_id: canary, metrics: [{ source_id: canary, trace_ref: canary, stage: "stage", windows: { "1m": { p99_ms: 1, secret: canary } } }] }], latency_error: canary } }, owner);
  const output = JSON.stringify(state);
  assert.ok(!output.includes(canary)); assert.ok(!output.includes("raw-account")); assert.ok(!output.includes("public_key"));
  assert.equal(state.read_only, true); assert.equal(state.strategies[0].account, publicScope(owner, "raw-account"));
  assert.notEqual(state.timefolio[0].id, state.timefolio[1].id);
  assert.notEqual(state.observability.latency[0].process_id, "private");
  assert.equal(state.markets[0].book.bids.length, 1);
  assert.equal(state.observability.pnl[0].account_scope, publicScope(owner, "raw-account"));
  const distinctScopes = publicPnl([{ account_scope: "raw-account-a" }, { account_scope: "raw-account-b" }], owner).rows.map(row => row.account_scope);
  assert.notEqual(distinctScopes[0], distinctScopes[1]);
  assert.equal(publicPnl([{ account_scope: "abcdef0123456789abcdef01" }], owner).rows[0].account_scope, "abcdef0123456789abcdef01");
  const pnl = JSON.stringify(publicPnl([{ key: "ok", account_scope: canary, envelope: canary, equity_curve: [{ ts: 1, secret: canary }] }], owner));
  const orders = JSON.stringify(publicOrders([{ id: canary, account: canary, credential: canary }], owner));
  assert.ok(!pnl.includes(canary)); assert.ok(!orders.includes(canary));
});

test("three concurrent SSE viewers receive bounded streams and reconnect independently", async () => {
  const gateway = new ViewerGateway(base), cookie = await signedCookie(gateway), hits = new Map();
  const dispatch = async req => {
    const n = (hits.get(req.url) || 0) + 1; hits.set(req.url, n);
    if (req.headers.has("x-test-fail")) throw new Error("private database stack canary");
    return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode("data: {\"ok\":true}\n\n")); controller.close(); } }), { headers: { "content-type": "text/event-stream" } });
  };
  const streams = await Promise.all([1, 2, 3].map(() => gateway.fetch(request("/api/observability/stream", { cookie }), dispatch)));
  assert.ok(streams.every(response => response.status === 200));
  assert.ok(streams.every(response => response.headers.get("cache-control").includes("no-store")));
  assert.deepEqual(await Promise.all(streams.map(async response => await response.text())), Array(3).fill("data: {\"ok\":true}\n\n"));
  const reconnect = await gateway.fetch(request("/api/observability/stream", { cookie }), dispatch);
  assert.equal(reconnect.status, 200); assert.equal((await reconnect.text()).includes("data:"), true);
  const failure = await gateway.fetch(request("/api/observability/stream", { cookie, headers: { "x-test-fail": "1" } }), dispatch);
  assert.equal(failure.status, 503); assert.ok(!(await failure.text()).includes("database stack"));
});

test("SSE wrapper is pull-based, cancels on abort/timeout, and clears its timer", async () => {
  const gateway = new ViewerGateway(base), cookie = await signedCookie(gateway);
  const originalSetTimeout = globalThis.setTimeout, originalClearTimeout = globalThis.clearTimeout;
  const callbacks = [], cleared = []; let reads = 0, canceled = 0;
  globalThis.setTimeout = (callback, delay) => { callbacks.push({ callback, delay, id: callbacks.length + 1 }); return callbacks.length; };
  globalThis.clearTimeout = id => { cleared.push(id); };
  try {
    const aborter = new AbortController();
    const response = await gateway.fetch(new Request(base.VIEWER_ORIGIN + "/api/observability/stream", { headers: { cookie }, signal: aborter.signal }), async () => new Response(new ReadableStream({
      pull(controller) { reads++; controller.enqueue(new TextEncoder().encode("data: x\n\n")); }, cancel() { canceled++; },
    }), { headers: { "content-type": "text/event-stream" } }));
    assert.equal(response.status, 200); assert.equal(callbacks.length, 1); assert.ok(callbacks[0].delay <= 60_000);
    await new Promise(resolve => originalSetTimeout(resolve, 20));
    assert.ok(reads <= 2, `upstream read count grew to ${reads} without a client pull`);
    const reader = response.body.getReader();
    assert.ok((await reader.read()).value.length > 0);
    await reader.cancel();
    assert.ok(canceled >= 1); assert.deepEqual(cleared, [1]);

    const timed = await gateway.fetch(request("/api/observability/stream", { cookie }), async () => new Response(new ReadableStream({
      pull(controller) { controller.enqueue(new TextEncoder().encode("data: y\n\n")); }, cancel() { canceled++; },
    }), { headers: { "content-type": "text/event-stream" } }));
    const timer = callbacks[callbacks.length - 1]; timer.callback();
    const timedReader = timed.body.getReader();
    await timedReader.read();
    assert.equal((await timedReader.read()).done, true);
    assert.ok(cleared.includes(timer.id));
  } finally { globalThis.setTimeout = originalSetTimeout; globalThis.clearTimeout = originalClearTimeout; }
});

test("encrypted order cursor is opaque, owner/section bound, tamper resistant and expires", async () => {
  const secret = "cursor-secret-is-independent-and-long-enough", claims = { owner, section: "crypto", updated_ms: 1234, id: "private-order-id" };
  const token = await sealOrderCursor(claims, secret);
  assert.ok(!token.includes(claims.id)); assert.deepEqual(await openOrderCursor(token, secret, owner, "crypto"), { ...claims, exp: Math.floor(Date.now() / 1000) + 600 });
  assert.equal(await openOrderCursor(token, secret + "rotated", owner, "crypto"), null);
  assert.equal(await openOrderCursor(token, secret, "fedcba9876543210fedcba98", "crypto"), null);
  assert.equal(await openOrderCursor(token, secret, owner, "stocks"), null);
  const ciphertextBytes = Buffer.from(token, "base64url");
  ciphertextBytes[ciphertextBytes.length - 1] ^= 1;
  const altered = ciphertextBytes.toString("base64url");
  assert.equal(await openOrderCursor(altered, secret, owner, "crypto"), null);
  const originalNow = Date.now;
  try { Date.now = () => originalNow() + 601_000; assert.equal(await openOrderCursor(token, secret, owner, "crypto"), null); }
  finally { Date.now = originalNow; }
});

test("production public dispatcher invokes the guarded handler with a viewer capability", async () => {
  const unique = { ...base, VIEWER_ORIGIN: "https://dispatcher.example" };
  const authRequest = new Request(unique.VIEWER_ORIGIN + "/api/state", { headers: { cookie: "" } });
  assert.equal((await publicFetch(authRequest, unique, async () => new Response("bad"))).status, 401);
  const loginRequest = new Request(unique.VIEWER_ORIGIN + "/viewer/session", { method: "POST", headers: { origin: unique.VIEWER_ORIGIN, "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ token }) });
  const issued = await publicFetch(loginRequest, unique, async () => new Response("bad"));
  const session = cookieFrom(issued);
  const authorized = await publicFetch(new Request(unique.VIEWER_ORIGIN + "/api/state", { headers: { cookie: session } }), unique,
    async (_request, capability) => Response.json({ kind: capability.kind, owner: capability.owner }));
  assert.deepEqual(await authorized.json(), { kind: "viewer", owner });
});

// Public redaction must preserve valid exact scientific Decimal notation.
test("scientific Decimal strings survive public price and curve projection", () => {
  const projected = publicSnapshot({ markets: [{ price: "1E-8", book: { bids: [["1E-8", "2E+3"]], asks: [] }, trades: [{ price: "1E-8", quantity: "2E+3" }] }],
    observability: { pnl: [{ account_scope: "a".repeat(24), equity_curve: [{ ts: 1, net_pnl: "-7E-18" }] }], latency: [] } }, owner);
  assert.equal(projected.markets[0].price, "1E-8");
  assert.deepEqual(projected.markets[0].book.bids, [["1E-8", "2E+3"]]);
  assert.equal(projected.observability.pnl[0].equity_curve[0].net_pnl, "-7E-18");
  assert.equal(publicOrders([{ price: "1E-8", quantity: "2E+3" }], owner).rows[0].price, "1E-8");
});

test("HTTP redirects never echo credential query parameters", async () => {
  const gateway = new ViewerGateway(base);
  const response = await gateway.fetch(new Request(base.VIEWER_ORIGIN.replace("https:", "http:") + "/api/state?token=PRIVATE-QUERY-CANARY"), async () => new Response("must not dispatch"));
  assert.equal(response.status, 403);
  assert.equal(response.headers.get("location"), null);
  assert.ok(!(await response.text()).includes("PRIVATE-QUERY-CANARY"));
});
