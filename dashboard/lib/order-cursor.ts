const encoder = new TextEncoder();
const b64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
const unb64 = (value: string) => Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
type Claims = { owner: string; section: string; updated_ms: number; id: string; exp: number };

async function key(secret: string) {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(`vt-order-cursor:${secret}`));
  return crypto.subtle.importKey("raw", digest, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function sealOrderCursor(claims: Omit<Claims, "exp">, secret: string) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const exp = Math.floor(Date.now() / 1000) + 600;
  const plaintext = encoder.encode(JSON.stringify({ ...claims, exp }));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await key(secret), plaintext));
  const packed = new Uint8Array(iv.length + ciphertext.length); packed.set(iv); packed.set(ciphertext, iv.length);
  return b64(packed);
}

export async function openOrderCursor(value: string, secret: string, owner: string, section: string): Promise<Claims | null> {
  if (!/^[A-Za-z0-9_-]{40,512}$/.test(value)) return null;
  try {
    const packed = unb64(value); if (packed.length < 29) return null;
    const iv = packed.slice(0, 12), ciphertext = packed.slice(12);
    const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, await key(secret), ciphertext);
    const claims = JSON.parse(new TextDecoder().decode(plaintext)) as Claims;
    const now = Math.floor(Date.now() / 1000);
    if (claims.owner !== owner || claims.section !== section || !Number.isSafeInteger(claims.updated_ms) ||
      typeof claims.id !== "string" || claims.id.length > 256 || !Number.isSafeInteger(claims.exp) || claims.exp <= now || claims.exp > now + 600) return null;
    return claims;
  } catch { return null; }
}
