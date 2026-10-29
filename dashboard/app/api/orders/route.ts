import { env } from "cloudflare:workers";
import { database, reply, viewerAuthorized } from "../../../lib/server";
import { publicOrders } from "../../../lib/public-projection";
import { getRequestCapability } from "../../../lib/viewer-context";
import { openOrderCursor, sealOrderCursor } from "../../../lib/order-cursor";

export async function GET(request: Request) {
  if (!viewerAuthorized(request)) return reply({ error: "unauthorized" }, 401);
  const params = new URL(request.url).searchParams;
  const section = params.get("section") || "crypto";
  if (!["stocks", "crypto", "timefolio"].includes(section)) return reply({ error: "invalid section" }, 400);
  const owner = getRequestCapability()?.owner || "";
  const token = params.get("cursor");
  let updatedMs = Date.now() + 60_000, beforeId = "";
  if (params.has("cursor")) {
    if (!token) return reply({ error: "invalid cursor" }, 400);
    if (!env.VIEWER_SESSION_SECRET) return reply({ error: "cursor unavailable" }, 503);
    const cursor = await openOrderCursor(token, env.VIEWER_SESSION_SECRET, owner, section);
    if (!cursor) return reply({ error: "invalid cursor" }, 400);
    updatedMs = cursor.updated_ms; beforeId = cursor.id;
  }
  const rows = await database().prepare("SELECT id,payload,updated_ms FROM orders WHERE section=? AND (updated_ms<? OR (updated_ms=? AND id<?)) ORDER BY updated_ms DESC,id DESC LIMIT 201")
    .bind(section, updatedMs, updatedMs, beforeId).all<{ id: string; payload: string; updated_ms: number }>();
  const page = rows.results.slice(0, 200);
  const nextCursor = rows.results.length > 200 && page.length && env.VIEWER_SESSION_SECRET
    ? await sealOrderCursor({ owner, section, updated_ms: page[page.length - 1].updated_ms, id: page[page.length - 1].id }, env.VIEWER_SESSION_SECRET)
    : null;
  return reply({ ...publicOrders(page.map(row => row.payload), owner), truncated: nextCursor !== null, next_cursor: nextCursor });
}
