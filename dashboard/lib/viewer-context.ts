import { AsyncLocalStorage } from "node:async_hooks";

export type RequestCapability = { kind: "viewer" | "private"; owner: string; expiresAt: number };
const current = new AsyncLocalStorage<RequestCapability | undefined>();

export function runWithRequestCapability<T>(capability: RequestCapability | undefined, run: () => T): T {
  return current.run(capability, run);
}

export function getRequestCapability(): RequestCapability | undefined {
  return current.getStore();
}

export function isPrivateCapability(): boolean {
  const value = current.getStore();
  return !!value && value.kind === "private" && value.expiresAt > Date.now();
}

export function isViewerCapability(owner: string): boolean {
  const value = current.getStore();
  return !!value && (value.kind === "viewer" || value.kind === "private") &&
    value.owner === owner && value.expiresAt > Date.now();
}
