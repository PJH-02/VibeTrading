import { database, ingestAuthorized, reply } from "../../../lib/server";

export async function POST(request: Request) {
  if (!await ingestAuthorized(request)) return reply({ error: "unauthorized" }, 401);
  if (Number(request.headers.get("content-length") || 0) > 2_000_000) return reply({ error: "payload too large" }, 413);
  const text = await request.text();
  if (text.length > 2_000_000) return reply({ error: "payload too large" }, 413);
  let data;
  try { data = JSON.parse(text); } catch { return reply({ error: "invalid JSON" }, 400); }
  if (data.schema_version !== 1 || !Number.isSafeInteger(data.generated_ms) ||
      !Array.isArray(data.markets) || data.markets.length > 512 ||
      !Array.isArray(data.orders) || data.orders.length > 1000 ||
      !Array.isArray(data.strategies) || data.strategies.length > 100 ||
      !Array.isArray(data.timefolio) || data.timefolio.length > 50 ||
      Math.abs(Date.now() - data.generated_ms) > 120_000) return reply({ error: "invalid snapshot" }, 400);
  const db = database();
  const now = Date.now();
  const statements = [db.prepare(`INSERT INTO snapshots VALUES ('server', ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET generated_ms=excluded.generated_ms, received_ms=excluded.received_ms, payload=excluded.payload
    WHERE excluded.generated_ms > snapshots.generated_ms`).bind(data.generated_ms, now, JSON.stringify({ ...data, orders: undefined }))];
  for (const order of data.orders) {
    if (typeof order.id !== "string" || !["stocks", "crypto", "timefolio"].includes(order.section) || !Number.isSafeInteger(order.updated_ms)) return reply({ error: "invalid order" }, 400);
    statements.push(db.prepare(`INSERT INTO orders VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE
      SET section=excluded.section, updated_ms=excluded.updated_ms, payload=excluded.payload WHERE excluded.updated_ms >= orders.updated_ms`)
      .bind(order.id, order.section, order.updated_ms, JSON.stringify(order)));
  }
  const bucket = Math.floor(data.generated_ms / 60_000) * 60_000;
  for (const strategy of data.strategies) {
    if (typeof strategy.id === "string" && strategy.realized_pnl != null) statements.push(db.prepare(`INSERT OR IGNORE INTO pnl_samples VALUES (?, ?, ?)`)
      .bind(strategy.id, bucket, JSON.stringify({ ...strategy, ts: bucket })));
  }
  // Bounded housekeeping; market ticks are deliberately not archived here.
  if (bucket % 600_000 < 60_000) statements.push(db.prepare("DELETE FROM pnl_samples WHERE bucket < ?").bind(now - 30 * 86400_000));
  // D1 batch query counts stay below the per-request limit.
  for (let offset = 1; offset < statements.length; offset += 80) await db.batch(statements.slice(offset, offset + 80));
  await statements[0].run();
  const accountRows = await db.prepare("SELECT id, label, envelope, updated_ms FROM accounts WHERE active=1 LIMIT 50").all();
  return reply({ ok: true, accounts: accountRows.results });
}
