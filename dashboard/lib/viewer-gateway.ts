import { isPrivateCapability, runWithRequestCapability, type RequestCapability } from "./viewer-context";

export type GatewayEnv = {
  VIEWER_ORIGIN?: string; VIEWER_TOKEN_SHA256?: string; VIEWER_SESSION_SECRET?: string;
  VIEWER_OWNER_SCOPE?: string; PRIVATE_SERVICE_TOKEN_SHA256?: string;
};
type GatewayConfig = { origin: string; tokenHash: string; secret: string; owner: string };
type Dispatch = (request: Request, capability?: RequestCapability) => Promise<Response>;
const COOKIE = "__Host-vt_viewer";
const SESSION_SECONDS = 600;
const encoder = new TextEncoder();
const b64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
const unb64 = (value: string) => Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
const json = (body: unknown, status: number, headers: HeadersInit = {}) => Response.json(body, { status, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", ...Object.fromEntries(new Headers(headers)) } });
const secureEqual = (a: Uint8Array, b: Uint8Array) => { if (a.length !== b.length) return false; let diff = 0; for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i]; return diff === 0; };
const sha256 = async (value: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
const allowReads = new Set(["/", "/api/state", "/api/pnl", "/api/orders", "/api/observability/stream"]);
const blockedMutations = new Set(["/api/accounts", "/api/ingest"]);
const querySecret = /(?:token|secret|authorization|credential|password|session|bearer|jwt|api[_-]?key)/i;
const form = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Private dashboard sign in</title><body><main><h1>Private dashboard</h1><form method="post" action="/viewer/session"><label>Viewer credential <input name="token" type="password" required autocomplete="current-password" maxlength="256"></label><button type="submit">Sign in</button></form></main></body></html>`;
const commonHeaders = (headers = new Headers()) => {
  headers.set("X-Content-Type-Options", "nosniff"); headers.set("X-Frame-Options", "DENY"); headers.set("Referrer-Policy", "no-referrer");
  headers.set("Cache-Control", "private, no-store"); headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  headers.set("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'");
  return headers;
};
const invalid = () => json({ error: "unauthorized" }, 401, commonHeaders());

export class ViewerGateway {
  private rates = new Map<string, { minute: number; requests: number; loginCount: number }>();
  private env: GatewayEnv;
  constructor(env: GatewayEnv) { this.env = env; }

  async fetch(request: Request, dispatch: Dispatch): Promise<Response> {
    const config = await this.config();
    if (!config) return json({ error: "gateway unavailable" }, 503, commonHeaders());
    const url = new URL(request.url), expected = new URL(config.origin);
    if (url.host.toLowerCase() !== expected.host.toLowerCase()) return json({ error: "forbidden" }, 403, commonHeaders());
    if (url.protocol !== "https:") return new Response(null, { status: 308, headers: commonHeaders(new Headers({ Location: config.origin + url.pathname + url.search })) });
    if (request.headers.has("origin") && request.headers.get("origin") !== config.origin) return json({ error: "forbidden" }, 403, commonHeaders());
    if (url.href.length > 2048 || [...url.searchParams.keys()].some(key => querySecret.test(key))) return json({ error: "forbidden" }, 403, commonHeaders());
    const queryAllow: Record<string, Set<string>> = {
      "/api/orders": new Set(["section", "cursor"]), "/api/pnl": new Set(["key", "strategy"]),
    };
    const allowedQuery = queryAllow[url.pathname] || new Set<string>();
    if ([...url.searchParams.keys()].some(key => !allowedQuery.has(key))) return json({ error: "forbidden" }, 403, commonHeaders());
    if (url.pathname === "/api/pnl" && url.searchParams.has("key") && !/^[a-f0-9]{64}$/i.test(url.searchParams.get("key") || "")) return json({ error: "forbidden" }, 403, commonHeaders());
    if (url.pathname === "/api/pnl" && (url.searchParams.get("strategy") || "").length > 256) return json({ error: "forbidden" }, 403, commonHeaders());
    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    if (!this.rate(ip, url.pathname === "/viewer/session")) return json({ error: "rate limited" }, 429, commonHeaders(new Headers({ "Retry-After": "60" })));

    if (url.pathname === "/viewer/login") {
      if (request.method !== "GET") return json({ error: "forbidden" }, 403, commonHeaders());
      return new Response(form, { status: 200, headers: commonHeaders(new Headers({ "Content-Type": "text/html; charset=utf-8" })) });
    }
    if (url.pathname === "/viewer/session") {
      if (request.method !== "POST" || request.headers.get("origin") !== config.origin || !/^application\/x-www-form-urlencoded(?:;|$)/i.test(request.headers.get("content-type") || "")) return invalid();
      const body = await this.readBody(request, 1024);
      if (body === null) return json({ error: "invalid request" }, 400, commonHeaders());
      let token = "";
      try { const params = new URLSearchParams(body); if ([...params.keys()].length !== 1 || !params.has("token")) return invalid(); token = params.get("token") || ""; } catch { return invalid(); }
      if (!/^[a-f0-9]{64}$/i.test(token) || !secureEqual(await sha256(token), Uint8Array.from(config.tokenHash.match(/../g)!, x => parseInt(x, 16)))) return invalid();
      const issued = Math.floor(Date.now() / 1000), claims = { owner: config.owner, iat: issued, exp: issued + SESSION_SECONDS, fp: b64(await sha256(config.tokenHash)) };
      const payload = b64(encoder.encode(JSON.stringify(claims))), sig = await this.sign(payload, config.secret), cookie = `${COOKIE}=${payload}.${sig}; Path=/; Max-Age=${SESSION_SECONDS}; Secure; HttpOnly; SameSite=Strict`;
      return new Response(null, { status: 303, headers: commonHeaders(new Headers({ Location: "/", "Set-Cookie": cookie })) });
    }
    const rootSession = url.pathname === "/" && request.method === "GET" ? await this.readSession(request, config) : null;
    if (url.pathname === "/" && request.method === "GET" && !rootSession) return new Response(null, { status: 303, headers: commonHeaders(new Headers({ Location: "/viewer/login" })) });
    if (blockedMutations.has(url.pathname)) return json({ error: "forbidden" }, 403, commonHeaders());
    if (request.method !== "GET" || (!allowReads.has(url.pathname) && !/^\/(?:assets|favicon\.svg)(?:\/|$)/.test(url.pathname))) return json({ error: "forbidden" }, 403, commonHeaders());
    const session = rootSession || await this.readSession(request, config);
    if (!session) return invalid();
    const capability: RequestCapability = { kind: "viewer", owner: config.owner, expiresAt: session.exp * 1000 };
    try {
      const response = await runWithRequestCapability(capability, () => dispatch(request, capability));
      const headers = commonHeaders(new Headers(response.headers));
      headers.delete("Set-Cookie");
      if (url.pathname === "/api/observability/stream" && response.body) return this.boundStream(response, headers, capability.expiresAt, request.signal);
      return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
    } catch { return json({ error: "temporarily unavailable" }, 503, commonHeaders()); }
  }

  private async config(): Promise<GatewayConfig | null> {
    const { VIEWER_ORIGIN: origin, VIEWER_TOKEN_SHA256: tokenHash, VIEWER_SESSION_SECRET: secret, VIEWER_OWNER_SCOPE: owner } = this.env;
    const privateHash = this.env.PRIVATE_SERVICE_TOKEN_SHA256;
    if (!origin || !tokenHash || !secret || !owner || !/^[a-f0-9]{64}$/i.test(tokenHash) || secret.length < 32 || !/^[a-f0-9]{24}$/.test(owner)) return null;
    if (privateHash && (!/^[a-f0-9]{64}$/i.test(privateHash) || privateHash.toLowerCase() === tokenHash.toLowerCase())) return null;
    const secretDigest = await sha256(secret);
    const tokenBytes = Uint8Array.from(tokenHash.match(/../g)!, x => parseInt(x, 16));
    if (secureEqual(secretDigest, tokenBytes)) return null;
    if (privateHash && secureEqual(secretDigest, Uint8Array.from(privateHash.match(/../g)!, x => parseInt(x, 16)))) return null;
    let normalizedOrigin: string;
    try { const url = new URL(origin); if (url.protocol !== "https:" || url.pathname !== "/" || url.search || url.hash || url.username || url.password) return null; normalizedOrigin = url.origin; } catch { return null; }
    return { origin: normalizedOrigin, tokenHash: tokenHash.toLowerCase(), secret, owner };
  }
  private rate(ip: string, login: boolean) {
    const minute = Math.floor(Date.now() / 60_000); let value = this.rates.get(ip);
    if (!value || value.minute !== minute) value = { minute, requests: 0, loginCount: 0 };
    value.requests++; if (login) value.loginCount++;
    this.rates.delete(ip); this.rates.set(ip, value);
    if (this.rates.size > 1024) this.rates.delete(this.rates.keys().next().value!);
    return value.requests <= 120 && value.loginCount <= 5;
  }
  private async readBody(request: Request, max: number) {
    const reader = request.body?.getReader(); if (!reader) return "";
    const chunks: Uint8Array[] = []; let size = 0;
    try { for (;;) { const { done, value } = await reader.read(); if (done) break; size += value.byteLength; if (size > max) { await reader.cancel(); return null; } chunks.push(value); } }
    catch { return null; }
    const all = new Uint8Array(size); let offset = 0; for (const part of chunks) { all.set(part, offset); offset += part.length; }
    try { return new TextDecoder("utf-8", { fatal: true }).decode(all); } catch { return null; }
  }
  private async sign(payload: string, secret: string) {
    const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return b64(new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(payload))));
  }
  private async readSession(request: Request, config: GatewayConfig) {
    const value = request.headers.get("cookie")?.split(";").map(x => x.trim()).find(x => x.startsWith(COOKIE + "="))?.slice(COOKIE.length + 1);
    if (!value) return null; const [payload, signature, extra] = value.split("."); if (!payload || !signature || extra !== undefined) return null;
    try {
      const expected = unb64(await this.sign(payload, config.secret)); if (!secureEqual(expected, unb64(signature))) return null;
      const claims = JSON.parse(new TextDecoder().decode(unb64(payload)));
      const now = Math.floor(Date.now() / 1000);
      if (claims.owner !== config.owner || claims.fp !== b64(await sha256(config.tokenHash)) || !Number.isSafeInteger(claims.iat) || !Number.isSafeInteger(claims.exp) || claims.exp <= now || claims.exp - claims.iat > SESSION_SECONDS || claims.iat > now + 30) return null;
      return claims as { owner: string; iat: number; exp: number; fp: string };
    } catch { return null; }
  }
  private boundStream(response: Response, headers: Headers, sessionExpiry: number, signal: AbortSignal) {
    const reader = response.body!.getReader(), duration = Math.max(0, Math.min(60_000, sessionExpiry - Date.now()));
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let controllerRef: ReadableStreamDefaultController<Uint8Array> | undefined;
    let onAbort: (() => void) | undefined;
    const cleanup = (cancel: boolean) => {
      if (closed) return Promise.resolve();
      closed = true;
      if (timer) clearTimeout(timer);
      if (onAbort) signal.removeEventListener("abort", onAbort);
      if (cancel) { try { return reader.cancel().then(() => undefined, () => undefined); } catch { return Promise.resolve(); } }
      return Promise.resolve();
    };
    const close = () => { if (closed) return; void cleanup(true); try { controllerRef?.close(); } catch {} };
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controllerRef = controller;
        onAbort = () => { void close(); };
        timer = setTimeout(() => { void close(); }, duration);
        signal.addEventListener("abort", onAbort, { once: true });
        if (signal.aborted) onAbort();
      },
      async pull(controller) {
        if (closed) return;
        if (signal.aborted || Date.now() >= sessionExpiry) {
          void cleanup(true); try { controller.close(); } catch {} return;
        }
        try {
          const item = await reader.read();
          if (closed) return;
          if (item.done) { void cleanup(false); controller.close(); return; }
          controller.enqueue(item.value);
        } catch { void cleanup(true); controller.error(new Error("stream unavailable")); }
      },
      cancel() { return cleanup(true); },
    });
    return new Response(stream, { status: response.status, headers });
  }
}

export async function withPrivateService(request: Request, env: GatewayEnv, dispatch: Dispatch) {
  if (!env.PRIVATE_SERVICE_TOKEN_SHA256 || !/^[a-f0-9]{64}$/i.test(env.PRIVATE_SERVICE_TOKEN_SHA256) || !env.VIEWER_OWNER_SCOPE || !/^[a-f0-9]{24}$/.test(env.VIEWER_OWNER_SCOPE)) return json({ error: "private service unavailable" }, 503);
  const auth = request.headers.get("x-private-service-authorization") || "";
  const supplied = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  const digest = await sha256(supplied), target = Uint8Array.from(env.PRIVATE_SERVICE_TOKEN_SHA256.match(/../g)!, x => parseInt(x, 16));
  if (!/^[a-f0-9]{64}$/i.test(supplied) || !secureEqual(digest, target)) return json({ error: "unauthorized" }, 401);
  const url = new URL(request.url);
  if (!((url.pathname === "/api/ingest" && request.method === "POST") || (url.pathname === "/api/accounts" && ["GET", "POST"].includes(request.method)))) return json({ error: "forbidden" }, 403);
  const cap: RequestCapability = { kind: "private", owner: env.VIEWER_OWNER_SCOPE, expiresAt: Date.now() + 30_000 };
  try { return await runWithRequestCapability(cap, () => dispatch(request, cap)); } catch { return json({ error: "temporarily unavailable" }, 503); }
}

// Stable named dispatchers are shared by the Worker default export and the
// offline/loopback integration harness, so the exercised guard is production's.
let workerGateway: ViewerGateway | undefined;
export function publicFetch(request: Request, env: GatewayEnv, dispatch: Dispatch) {
  workerGateway ??= new ViewerGateway(env);
  return workerGateway.fetch(request, dispatch);
}
export function privateFetch(request: Request, env: GatewayEnv, dispatch: Dispatch) {
  return withPrivateService(request, env, dispatch);
}
