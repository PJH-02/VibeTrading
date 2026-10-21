import { database, reply } from "../../../lib/server";

export async function GET(request: Request) {
  const id = new URL(request.url).searchParams.get("strategy");
  if (!id) return reply({ rows: [] });
  const rows = await database().prepare("SELECT payload FROM pnl_samples WHERE strategy_id=? AND bucket>? ORDER BY bucket LIMIT 1440")
    .bind(id, Date.now() - 86400_000).all<{ payload: string }>();
  return reply({ rows: rows.results.map(x => JSON.parse(x.payload)) });
}
