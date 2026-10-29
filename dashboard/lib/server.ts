import { env } from "cloudflare:workers";
import { isPrivateCapability, isViewerCapability, getRequestCapability } from "./viewer-context";

export function database() {
  if (!env.DB) throw new Error("DB binding unavailable");
  return env.DB;
}
export function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
}
export async function ingestAuthorized(request: Request) {
  if (!isPrivateCapability() || !env.INGEST_TOKEN) return false;
  const token = request.headers.get("authorization")?.replace(/^Bearer /, "") || "";
  const hash = async (s: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
  const [a, b] = await Promise.all([hash(token), hash(env.INGEST_TOKEN)]);
  return a.reduce((diff, byte, i) => diff | (byte ^ b[i]), 0) === 0;
}
export function viewerAuthorized(_request: Request) {
  const capability = getRequestCapability();
  return !!capability && isViewerCapability(env.VIEWER_OWNER_SCOPE || "");
}
export function browserWriteAuthorized(_request?: Request) {
  return isPrivateCapability();
}
