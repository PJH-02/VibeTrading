import { database, reply } from "../../../lib/server";

export async function GET() {
  const row = await database().prepare("SELECT payload, received_ms FROM snapshots WHERE id='server'").first<{ payload: string; received_ms: number }>();
  if (!row) return reply({ schema_version: 1, generated_ms: null, markets: [], strategies: [], timefolio: [], errors: [], connected: false });
  return reply({ ...JSON.parse(row.payload), received_ms: row.received_ms, connected: Date.now() - row.received_ms < 15000 });
}
