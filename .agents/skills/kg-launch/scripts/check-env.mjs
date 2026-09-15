#!/usr/bin/env node
/**
 * kg-launch: cross-platform environment check for the Knowledge Graph System.
 *
 * Verifies prerequisites before `docker compose up -d` + `npm run dev`:
 *   - platform detection (incl. Apple Silicon / Rosetta note)
 *   - Python venv presence + version
 *   - Docker / Compose presence + container state
 *   - backend/.env sanity (presence, JWT_SECRET not a placeholder,
 *     NEO4J_PASSWORD aligned with the compose default or root .env,
 *     at least one LLM/embedding key configured)
 *   - frontend + root node_modules
 *
 * Prints PASS/WARN/FAIL lines (ASCII only — never echoes secret values).
 * Exit 0 = ready to launch, 1 = something blocking needs setup.
 */
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = resolve(fileURLToPath(import.meta.url), "..");
const REPO_ROOT = resolve(SCRIPT_DIR, "..", "..", "..", "..");

const results = { pass: 0, warn: 0, fail: 0 };
const ok = (msg) => { results.pass++; console.log(`  [PASS] ${msg}`); };
const warn = (msg) => { results.warn++; console.log(`  [WARN] ${msg}`); };
const fail = (msg) => { results.fail++; console.log(`  [FAIL] ${msg}`); };

function sh(cmd, args) {
  try {
    const r = spawnSync(cmd, args, { encoding: "utf8", timeout: 15000, windowsHide: true });
    return { ok: r.status === 0, out: (r.stdout || "").trim() };
  } catch {
    return { ok: false, out: "" };
  }
}

function readEnv(file) {
  if (!existsSync(file)) return {};
  const out = {};
  try {
    for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m) out[m[1]] = m[2].trim().replace(/^["']|["']$/g, "");
    }
  } catch {}
  return out;
}

// --- platform banner ---
const plat = process.platform;
const platLabel = { win32: "Windows", darwin: "macOS", linux: "Linux" }[plat] || plat;
const archLabel = process.arch === "arm64" ? "ARM64" : "x64";
const isAppleSilicon = plat === "darwin" && process.arch === "arm64";
console.log(`\n[kg-launch] platform: ${platLabel} ${archLabel} | node ${process.version} | repo: ${REPO_ROOT}\n`);

if (isAppleSilicon) {
  warn("Apple Silicon: neo4j/chromadb images are amd64, Docker Desktop needs Rosetta 2 (`softwareupdate --install-rosetta --agree-to-license`).");
}

// --- python venv ---
const bins = plat === "win32" ? ["Scripts\\python.exe"] : ["bin/python"];
const venvCandidates = [
  join(REPO_ROOT, ".venv"),
  join(REPO_ROOT, "..", ".venv"),
  join(REPO_ROOT, "backend", ".venv"),
];
let python = "";
let venvDir = "";
for (const dir of venvCandidates) {
  for (const bin of bins) {
    const p = join(dir, bin);
    if (existsSync(p)) { python = p; venvDir = dir; break; }
  }
  if (python) break;
}
if (python) {
  const v = sh(python, ["--version"]).out || "?";
  ok(`Python venv found: ${venvDir} (${v})`);
} else {
  fail("No Python venv found. Create one first: `python3.11 -m venv .venv` then `.venv/bin/python -m pip install -r requirements.txt` (Windows: `.venv/Scripts/python.exe`).");
  const probe = plat === "win32" ? ["python", "--version"] : ["python3.11", "--version"];
  const r = sh(probe[0], probe.slice(1));
  if (r.ok) ok(`System python available: ${r.out} — can create the venv.`);
  else warn("No usable system python on PATH for creating the venv.");
}

// --- docker / compose ---
const dv = sh("docker", ["compose", "version"]);
if (!dv.ok) {
  fail("Docker/Compose not available. Install Docker Desktop and ensure the CLI is on PATH.");
} else {
  const ver = dv.out.match(/v?[\d.]+/)?.[0] || dv.out.split("\n")[0];
  ok(`Docker Compose available (${ver})`);
  const ps = sh("docker", ["compose", "ps"]);
  if (!ps.ok) {
    warn("`docker compose ps` failed — daemon may be stopped. Start Docker, then `docker compose up -d`.");
  } else {
    const state = (name) => {
      const line = (ps.out.split("\n") || []).find((l) => l.includes(name));
      if (!line) return "absent";
      return /(healthy|Up|running)/i.test(line) ? "running" : "down";
    };
    const n = state("neo4j_kg");
    const c = state("chromadb_kg");
    if (n === "running") ok("neo4j_kg running");
    else if (n === "absent") warn("neo4j_kg not started — run `docker compose up -d` (first pull may take minutes).");
    else warn("neo4j_kg present but not healthy — `docker compose ps`, first start ~30-60s.");
    if (c === "running") ok("chromadb_kg running");
    else if (c === "absent") warn("chromadb_kg not started — run `docker compose up -d`.");
    else warn("chromadb_kg present but not healthy — `docker compose ps`; port 8000 may be taken.");
  }
}

// --- backend/.env ---
const env = readEnv(join(REPO_ROOT, "backend", ".env"));
const rootEnv = readEnv(join(REPO_ROOT, ".env"));

if (!existsSync(join(REPO_ROOT, "backend", ".env"))) {
  fail("backend/.env missing. Copy backend/.env.example to backend/.env and fill: JWT_SECRET, NEO4J_PASSWORD, and at least one of SILICON_FLOW_API_KEY / BAILIAN_API_KEY.");
} else {
  ok("backend/.env present");
  const jwt = env.JWT_SECRET || "";
  const PLACEHOLDERS = new Set([
    "",
    "your-secret-key-change-this-in-production",
    "replace-me-with-a-strong-random-value",
  ]);
  if (PLACEHOLDERS.has(jwt)) {
    fail("JWT_SECRET is a known placeholder — the app refuses to start. Generate one: `node -e \"console.log(require('crypto').randomBytes(32).toString('base64url'))\"`");
  } else {
    ok(`JWT_SECRET set (length ${jwt.length}, never echoed)`);
  }
  const composePwd = rootEnv.NEO4J_PASSWORD || "12345678"; // docker-compose.yml default
  const appPwd = env.NEO4J_PASSWORD || "12345678";          // config.py default
  if (composePwd !== appPwd) {
    fail(`NEO4J_PASSWORD mismatch: compose uses "${rootEnv.NEO4J_PASSWORD ? "root .env custom" : "default 12345678"}" but backend/.env sets "${env.NEO4J_PASSWORD ? "custom" : "default"}". Backend cannot authenticate to Neo4j — make both equal.`);
  } else {
    ok("NEO4J_PASSWORD aligned (compose default/custom matches backend)");
  }
  const hasKey = !!(env.SILICON_FLOW_API_KEY || env.BAILIAN_API_KEY || env.KIMI_API_KEY);
  if (hasKey) ok("At least one LLM/embedding API key configured");
  else warn("No LLM/embedding API key in backend/.env — search/chat will fail until SILICON_FLOW_API_KEY or BAILIAN_API_KEY is set.");
}

// --- node deps ---
if (existsSync(join(REPO_ROOT, "frontend", "node_modules"))) ok("frontend dependencies installed");
else warn("frontend/node_modules missing — run `npm install` in frontend/");
if (existsSync(join(REPO_ROOT, "node_modules"))) ok("root dependencies installed (concurrently)");
else warn("root node_modules missing (concurrently) — run `npm install` at repo root");

// --- summary ---
console.log(`\n[kg-launch] ${results.pass} pass, ${results.warn} warn, ${results.fail} fail`);
if (results.fail > 0) {
  console.log("[kg-launch] Fix the FAIL items above, then re-run. See references/troubleshooting.md for known issues.");
  process.exit(1);
}
console.log("[kg-launch] Environment ready: `docker compose up -d` then `npm run dev`.");
process.exit(0);
