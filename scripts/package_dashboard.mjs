// Portable Windows fallback for the Sites Bash archive wrapper.
// Use the same official build normalizer and artifact layout.
import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
const project = path.resolve(process.argv[2]);
const archive = path.resolve(process.argv[3]);
const plugin = "C:/Users/박제형/.codex/plugins/cache/openai-curated-remote/sites/1.0.0-b/skills/sites/scripts";
mkdirSync(path.join(project, ".sites-runtime"), { recursive: true });
const stage = mkdtempSync(path.join(project, ".sites-runtime", "package-"));
const normalized = spawnSync(process.execPath, [path.join(plugin, "prepare-site-build.cjs"), project, path.join(stage, "dist")], { encoding: "utf8" });
if (normalized.status !== 0) throw new Error(normalized.stderr);
const hosting = JSON.parse(readFileSync(path.join(project, ".openai/hosting.json"), "utf8"));
const built = JSON.parse(readFileSync(path.join(project, "dist/.openai/hosting.json"), "utf8"));
if (built.artifact_metadata) hosting.artifact_metadata = built.artifact_metadata;
mkdirSync(path.join(stage, "dist/.openai"), { recursive: true });
writeFileSync(path.join(stage, "dist/.openai/hosting.json"), JSON.stringify(hosting, null, 2));
cpSync(path.join(project, "drizzle"), path.join(stage, "dist/.openai/drizzle"), { recursive: true });
mkdirSync(path.dirname(archive), { recursive: true });
const packed = spawnSync("tar", ["-C", stage, "-czf", archive, "dist"], { encoding: "utf8" });
if (packed.status !== 0) throw new Error(packed.stderr);
const listing = spawnSync("tar", ["-tzf", archive], { encoding: "utf8" });
if (!listing.stdout.includes("dist/.openai/hosting.json") || !listing.stdout.includes("dist/server/index.js")) throw new Error("Invalid Sites archive layout");
const head = spawnSync("git", ["-C", project, "rev-parse", "HEAD"], { encoding: "utf8" }).stdout.trim();
const clean = spawnSync("git", ["-C", project, "status", "--porcelain"], { encoding: "utf8" });
if (clean.stdout.trim()) throw new Error("Source changed after pushing");
console.log(JSON.stringify({ project_id: hosting.project_id, commit_sha: head, archive }));
