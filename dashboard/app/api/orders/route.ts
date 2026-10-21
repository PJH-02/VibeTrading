import { database, reply } from "../../../lib/server";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const section = params.get("section") || "crypto";
  if (!["stocks", "crypto", "timefolio"].includes(section)) return reply({ error: "invalid section" }, 400);
  const cursor = Number(params.get("before") || Date.now() + 60000);
  if (!Number.isSafeInteger(cursor)) return reply({ error: "invalid cursor" }, 400);
  const beforeId = params.get("before_id") || "";
  const rows = await database().prepare("SELECT payload,updated_ms FROM orders WHERE section=? AND (updated_ms<? OR (updated_ms=? AND id<?)) ORDER BY updated_ms DESC,id DESC LIMIT 201")
    .bind(section, cursor, cursor, beforeId).all<{ payload: string; updated_ms: number }>();
  return reply({ rows: rows.results.slice(0, 200).map(row => JSON.parse(row.payload)), truncated: rows.results.length > 200 });
}
