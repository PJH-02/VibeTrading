import { env } from "cloudflare:workers";

export function database() {
  if (!env.DB) throw new Error("DB binding unavailable");
  return env.DB;
}
export function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
}
export async function ingestAuthorized(request: Request) {
  if (!env.INGEST_TOKEN) return false;
  const token = request.headers.get("authorization")?.replace(/^Bearer /, "") || "";
  const hash = async (s: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
  const [a, b] = await Promise.all([hash(token), hash(env.INGEST_TOKEN)]);
  return a.reduce((diff, byte, i) => diff | (byte ^ b[i]), 0) === 0;
}
export function browserWriteAuthorized(request: Request) {
  // Sites dispatch enforces owner-private access. The ingest service has no visitor identity.
  const origin = request.headers.get("origin");
  return !!request.headers.get("oai-authenticated-user-id") && origin === new URL(request.url).origin;
}
