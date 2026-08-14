import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.join(ROOT, "build_sap_authoritative_repair_payments_xlsx.py");

const pythonCandidates = [
  process.env.PYTHON,
  "python",
  "py",
].filter(Boolean);

let lastResult = null;
for (const python of pythonCandidates) {
  const result = spawnSync(python, [SCRIPT], {
    cwd: ROOT,
    stdio: "inherit",
    shell: false,
  });
  lastResult = result;
  if (!result.error && result.status === 0) {
    process.exit(0);
  }
  if (result.error && result.error.code === "ENOENT") {
    continue;
  }
  process.exit(result.status ?? 1);
}

if (lastResult?.error) {
  console.error(lastResult.error.message);
}
console.error("Python was not found in PATH.");
process.exit(1);
