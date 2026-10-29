import { database, browserWriteAuthorized, reply } from "../../../lib/server";

export async function GET(request: Request) {
  if (!browserWriteAuthorized(request)) return reply({ error: "forbidden" }, 403);
  const rows = await database().prepare("SELECT id,label,active,updated_ms FROM accounts ORDER BY updated_ms DESC LIMIT 50").all();
  return reply({ rows: rows.results });
}
export async function POST(request: Request) {
  if (!browserWriteAuthorized(request)) return reply({ error: "로그인과 동일 출처 요청이 필요합니다." }, 403);
  const text = await request.text();
  if (text.length > 12000) return reply({ error: "request too large" }, 413);
  let body;
  try { body = JSON.parse(text); } catch { return reply({ error: "invalid JSON" }, 400); }
  const db = database();
  if (body.action === "delete" && typeof body.id === "string") {
    await db.prepare("UPDATE accounts SET active=0, envelope='', updated_ms=? WHERE id=?").bind(Date.now(), body.id).run();
    return reply({ ok: true });
  }
  const envelope = body.envelope;
  if (typeof body.label !== "string" || !body.label.trim() || body.label.length > 50 ||
      envelope?.version !== 1 || ![envelope.wrapped_key, envelope.iv, envelope.ciphertext].every(x => typeof x === "string" && /^[A-Za-z0-9+/=]+$/.test(x))) {
    return reply({ error: "invalid encrypted account" }, 400);
  }
  const count = await db.prepare("SELECT COUNT(*) n FROM accounts WHERE active=1").first<{ n: number }>();
  if ((count?.n || 0) >= 20) return reply({ error: "계정은 최대 20개까지 등록할 수 있습니다." }, 400);
  const id = crypto.randomUUID();
  await db.prepare("INSERT INTO accounts VALUES (?, ?, ?, 1, ?)").bind(id, body.label.trim(), JSON.stringify(envelope), Date.now()).run();
  return reply({ ok: true, id });
}
