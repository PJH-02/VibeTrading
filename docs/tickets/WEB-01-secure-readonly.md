# WEB-01 bounded local secure dashboard candidate

Prerequisites: accepted OBS-01 and MOV-06. Existing ingress is Sites/Cloudflare
Worker + D1, built by Vinext. Ubuntu Compose contains only bridge/Timefolio
monitor; do not invent a second frontend or add Caddy/new services. No account,
server, DNS, firewall, production certificate, package-install or deployment
change is authorized. SSL result is LOCAL_TEST_ONLY; CUT-01 owns external checks.

Native gpt-6-luna worker after external cache-inclusive usage exceeded the user
stop threshold. Source edits only in an isolated public snapshot. Lead owns
architecture, source review, independent TLS/route/DB/build/regression validation,
receipt/status and final acceptance. No recursive delegation or self-approval.

Allowed dashboard paths: tsconfig.json (noEmit-only explicit TS extension support), build/sites-worker.ts, lib/{server,viewer-gateway,
viewer-context,public-projection,order-cursor}.ts, cloudflare-env.d.ts, app/api/{state,pnl,
orders,accounts,ingest,observability/stream}/route.ts, app/page.tsx and new
scripts/test-web-access.mjs. Allowed operational templates: deploy/WEB-01-access.md,
deploy/dashboard-public.env.example, scripts/dev/web_access_preflight.py and
new tests/refactoring/test_web01_preflight.py. No financial/runtime/fee/signing
source edits. Preserve the Ubuntu bridge and existing deployment configuration.

## Lead-approved boundaries

- Default public Worker fails closed without explicit HTTPS origin, owner scope,
  high-entropy viewer-token SHA256 and independent session signing secret.
  Single-owner D1 deployment, no multi-tenant claim. Exact host/origin, fixed
  method/path allowlist. No inbound OAI/forwarded header authentication.
- Same-origin bounded credential exchange establishes a signed short session in
  a __Host- cookie: Secure, HttpOnly, SameSite=Strict, lifetime <=600 seconds.
  No token in URLs/browser storage/logs; strong random token, not a human password.
  Bounded per-isolate rate state; distributed ingress/rate policy is a deployment
  check, not proved by one local isolate. TLS redirect and defensive response
  headers; no public backend listener or container credential mounts added.
- Authenticated GET only for dashboard, assets, state/PnL/orders/SSE. Public
  accounts and ingest are rejected regardless of any supplied viewer/service
  credential. Session POST authenticates only, never trading. Every API handler
  checks a request-scoped capability established by verified public/private
  dispatch; client identity headers alone cannot manufacture that capability.
- Fixed scalar/structured response projections, pseudonymous stable identifiers,
  preserve distinct accounts/strategies/processes, bounded history and stream.
  Strip public key/encrypted account envelopes/error stacks/private metadata.
  PnL key filters and order tie-break pagination must continue to function.
  SSE expiry/60-second lifetime, cancellation and slow-client backpressure must
  not create an unbounded buffer. A disabled dashboard never stops trading.
- Private named WorkerEntrypoint service capability handles only authenticated
  ingest/account admin. Separate private-service credential plus existing ingest
  credential. No public HTTP route to that entrypoint. Encrypted account reply
  remains available to the private Ubuntu bridge/Timefolio path. Existing bridge
  HTTPS/Sites credential contract is preserved, never repointed to viewer host.

## Deployment gate

Actual private Sites/gateway-to-service binding connectivity is NOT established
by local emulation. DEP-01/CUT-01 must validate existing gateway routing, secret
binding, URL, managed TLS/renewal, DNS, origin bypass protection, global rate
policy and no public mutation path. Preflight must block missing DNS/auth/TLS,
private gateway proof reference, public ingest host or directly exposed backend.
No claim of a production-working public URL until those external gates pass.

Independent acceptance: Node tests of actual guard/context/projections; actual
route SQL via existing migration and SQLite; loopback TLS with self-signed test
CA, unauthenticated/forged/expired/mutation rejection, private ingest/envelope,
3 clients/SSE/reconnect/slow readers, backend failure/restart and certificate
rotation; full dashboard Vinext build, offline Python regression, existing Compose
configuration parse and static mount/port/credential isolation review.
