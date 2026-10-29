// CLI: `pnpm scan:bundle [dir]` (default apps/web/.next/static, the files Next serves to
// browsers). Run after `pnpm --filter web build`, in the same environment, so the actual
// values of the server secrets the build saw are searched for too. Exit 1 on a finding,
// 2 when there is nothing to scan (a scan of an empty directory proves nothing).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { findSecretsInBundle, SECRET_ENV_NAMES } from "./bundle-scan.ts";

const SCANNED = /\.(?:js|mjs|cjs|css|html|json|map|txt)$/;

const dir = process.argv[2] ?? "apps/web/.next/static";

function walk(root: string): string[] {
  return readdirSync(root, { recursive: true, encoding: "utf8" })
    .map((entry) => join(root, entry))
    .filter((path) => SCANNED.test(path) && statSync(path).isFile());
}

let paths: string[];
try {
  paths = walk(dir);
} catch {
  console.error(`bundle scan: ${dir} not found. Run \`pnpm --filter web build\` first.`);
  process.exit(2);
}
if (!paths.some((path) => path.endsWith(".js"))) {
  console.error(`bundle scan: no JavaScript under ${dir}; refusing to pass an empty scan.`);
  process.exit(2);
}

const files = paths.map((path) => ({
  path: relative(process.cwd(), path),
  content: readFileSync(path, "utf8"),
}));
const secretValues = Object.fromEntries(SECRET_ENV_NAMES.map((name) => [name, process.env[name]]));
const findings = findSecretsInBundle(files, secretValues);

if (findings.length > 0) {
  console.error(`bundle scan: ${findings.length} finding(s) in the client bundle:`);
  for (const { path, rule, detail } of findings) console.error(`  ${path}: ${rule} (${detail})`);
  process.exit(1);
}
console.log(`bundle scan: ${files.length} client files, no server secrets.`);
