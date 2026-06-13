// Decide whether web/dist needs rebuilding.
//   exit 1 -> dist is missing or stale (source newer) => caller should rebuild
//   exit 0 -> dist is up to date => caller can skip the build
// Cross-platform (Node, no bash). Used by the launcher scripts (새싹이.cmd / start.*).
import { existsSync, statSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webDir = dirname(scriptDir);
const distIndex = join(webDir, "dist", "index.html");

// No build yet -> must build.
if (!existsSync(distIndex)) process.exit(1);

const distMtime = statSync(distIndex).mtimeMs;

const watchedDirs = [join(webDir, "src"), join(webDir, "public")];
const watchedFiles = [
  join(webDir, "index.html"),
  join(webDir, "package.json"),
  join(webDir, "vite.config.ts"),
  join(webDir, "vite.config.js"),
  join(webDir, "tsconfig.json"),
];

let newest = 0;
function walk(dir) {
  if (!existsSync(dir)) return;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) walk(p);
    else {
      const m = statSync(p).mtimeMs;
      if (m > newest) newest = m;
    }
  }
}
for (const d of watchedDirs) walk(d);
for (const f of watchedFiles) {
  if (existsSync(f)) {
    const m = statSync(f).mtimeMs;
    if (m > newest) newest = m;
  }
}

// Source newer than the built bundle -> rebuild.
process.exit(newest > distMtime ? 1 : 0);
