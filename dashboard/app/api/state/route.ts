import { database, reply, viewerAuthorized } from "../../../lib/server";
import { publicSnapshot } from "../../../lib/public-projection";
import { getRequestCapability } from "../../../lib/viewer-context";

export async function GET(request: Request) {
  if (!viewerAuthorized(request)) return reply({ error: "unauthorized" }, 401);
  const row = await database().prepare("SELECT payload, received_ms FROM snapshots WHERE id='server'").first<{ payload: string; received_ms: number }>();
  const source = row ? { ...JSON.parse(row.payload), received_ms: row.received_ms, connected: Date.now() - row.received_ms < 15000 } : {};
  const owner = getRequestCapability()?.owner || "";
  return reply(publicSnapshot(source, owner));
}
