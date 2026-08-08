// Cross-platform backend launcher for the Knowledge Graph System.
// Finds the project venv (repo root / parent / backend) and runs uvicorn.
// Used by `npm run backend` and `npm run dev` so the same command works on
// Windows (cmd / PowerShell) and macOS/Linux without hardcoded paths.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(fileURLToPath(import.meta.url), "..", "..");
const backendDir = join(repoRoot, "backend");

// Candidate venv locations, in priority order.
const venvCandidates = [
  join(repoRoot, ".venv"),        // venv at repo root
  join(repoRoot, "..", ".venv"),  // venv at parent of repo (common when repo is a subfolder)
  join(backendDir, ".venv"),      // venv inside backend/
];

// Candidate python executables within a venv, by platform.
const bins =
  process.platform === "win32" ? ["Scripts\\python.exe"] : ["bin/python"];

let python = "";
for (const venv of venvCandidates) {
  for (const bin of bins) {
    const p = join(venv, bin);
    if (existsSync(p)) {
      python = resolve(p);
      break;
    }
  }
  if (python) break;
}

if (!python) {
  // Fallback to a python on PATH.
  python = process.platform === "win32" ? "python.exe" : "python3";
  console.warn(`[run-backend] No .venv found, falling back to '${python}' on PATH.`);
} else {
  console.log(`[run-backend] Using Python: ${python}`);
}

const args = [
  "-m", "uvicorn", "app.main:app",
  "--host", "0.0.0.0", "--port", "8001", "--reload",
];

const child = spawn(python, args, { stdio: "inherit", cwd: backendDir, shell: false });

child.on("error", (err) => {
  console.error("[run-backend] Failed to start backend:", err.message);
  process.exit(1);
});
child.on("exit", (code) => {
  process.exit(code ?? 0);
});
