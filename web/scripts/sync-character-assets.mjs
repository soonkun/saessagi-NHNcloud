// Sync character avatar PNGs from assets/character/saessagi/ -> web/public/avatars/
// Cross-platform (Node, no bash). Run automatically via package.json predev / prebuild hooks.
import { existsSync, mkdirSync, readdirSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webDir = dirname(scriptDir);
const srcDir = join(webDir, "..", "assets", "character", "saessagi");
const dstDir = join(webDir, "public", "avatars");

if (!existsSync(srcDir)) {
  console.log(`[sync-character-assets] source not found: ${srcDir} — skipping`);
  process.exit(0);
}

mkdirSync(dstDir, { recursive: true });
const pngs = readdirSync(srcDir).filter((f) => f.toLowerCase().endsWith(".png"));
for (const f of pngs) {
  copyFileSync(join(srcDir, f), join(dstDir, f));
}
console.log(`[sync-character-assets] synced ${pngs.length} PNGs -> ${dstDir}`);
