import { database, reply, viewerAuthorized } from "../../../../lib/server";
import { streamHasCapacity } from "../../../../lib/observability";
import { getRequestCapability } from "../../../../lib/viewer-context";
import { publicSnapshot } from "../../../../lib/public-projection";

export async function GET(request: Request) {
  if (!viewerAuthorized(request)) return reply({ error: "unauthorized" }, 401);
  const capability = getRequestCapability();
  if (!capability) return reply({ error: "unauthorized" }, 401);
  const owner = capability.owner;
  const encoder = new TextEncoder();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let lastReceived = -1;
  let lastFrame = Date.now();
  let controllerRef: ReadableStreamDefaultController<Uint8Array> | undefined;

  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controllerRef = controller;
      const stop = () => {
        stopped = true;
        if (timer) clearTimeout(timer);
        try { controller.close(); } catch { }
      };
      request.signal.addEventListener("abort", stop, { once: true });
      const poll = async () => {
        if (stopped) return;
        if (Date.now() >= capability.expiresAt) { stop(); return; }
        try {
          const row = await database().prepare("SELECT payload, received_ms FROM snapshots WHERE id='server'")
            .first<{ payload: string; received_ms: number }>();
          if (stopped) return;
          const hasCapacity = streamHasCapacity(controller.desiredSize);
          if (row && hasCapacity && row.received_ms !== lastReceived) {
            const snapshot = publicSnapshot({ ...JSON.parse(row.payload), received_ms: row.received_ms,
              connected: Date.now() - row.received_ms < 15000 }, owner);
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(snapshot)}\n\n`));
            lastReceived = row.received_ms;
            lastFrame = Date.now();
          } else if (hasCapacity && Date.now() - lastFrame >= 15000) {
            controller.enqueue(encoder.encode(": keepalive\n\n"));
            lastFrame = Date.now();
          }
        } catch {
          // A viewer stream reports a temporary read failure with a bounded
          // heartbeat; the polling GET remains independently available.
          if (!stopped && streamHasCapacity(controller.desiredSize) &&
              Date.now() - lastFrame >= 15000) {
            controller.enqueue(encoder.encode(": unavailable\n\n"));
            lastFrame = Date.now();
          }
        }
        if (!stopped) timer = setTimeout(poll, 2000);
      };
      void poll();
    },
    cancel() {
      stopped = true;
      if (timer) clearTimeout(timer);
    },
  });

  request.signal.addEventListener("abort", () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    try { controllerRef?.close(); } catch { }
  }, { once: true });

  return new Response(body, { headers: {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-store, no-transform",
    "X-Content-Type-Options": "nosniff",
    "X-Accel-Buffering": "no",
  } });
}
