declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
    INGEST_TOKEN?: string;
    VIEWER_ORIGIN?: string;
    VIEWER_TOKEN_SHA256?: string;
    VIEWER_SESSION_SECRET?: string;
    VIEWER_OWNER_SCOPE?: string;
    PRIVATE_SERVICE_TOKEN_SHA256?: string;
  }
}
