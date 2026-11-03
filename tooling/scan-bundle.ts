// CLI: `pnpm scan:bundle [dir]`. By default it scans what Next serves to browsers: every
// file in apps/web/.next/static, plus the prerendered pages and RSC payloads (`.html`,
// `.rsc`) in apps/web/.next/server/app, never the server-only JavaScript there. A `dir`
// argument scans that one directory instead. Run after `pnpm --filter web build`, in the
// same environment, so the actual values of the server secrets (and of any secret-sounding
// `NEXT_PUBLIC_` variable) the build saw are searched for too. Exit 1 on a finding, 2 when
// there is nothing to scan (a scan of an empty directory proves nothing).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { findSecretsInBundle, publicSecretValues, SECRET_ENV_NAMES } from "./bundle-scan.ts";

const SCANNED = /\.(?:js|mjs|cjs|css|html|json|map|txt)$/;
const PRERENDERED = /\.(?:html|rsc)$/;
const JAVASCRIPT = /\.[mc]?js$/;

const roots: ReadonlyArray<{ dir: string; pattern: RegExp; required: boolean }> = process.argv[2]
  ? [{ dir: process.argv[2], pattern: SCANNED, required: true }]
  : [
      { dir: "apps/web/.next/static", pattern: SCANNED, required: true },
      // Absent when no route is prerendered: then nothing there is served to browsers.
      { dir: "apps/web/.next/server/app", pattern: PRERENDERED, required: false },
    ];

function walk(root: string, pattern: RegExp): string[] {
  return readdirSync(root, { recursive: true, encoding: "utf8" })
    .map((entry) => join(root, entry))
    .filter((path) => pattern.test(path) && statSync(path).isFile());
}

const paths: string[] = [];
for (const { dir, pattern, required } of roots) {
  try {
    paths.push(...walk(dir, pattern));
  } catch {
    if (!required) continue;
    console.error(`bundle scan: ${dir} not found. Run \`pnpm --filter web build\` first.`);
    process.exit(2);
  }
}
if (!paths.some((path) => JAVASCRIPT.test(path))) {
  console.error("bundle scan: no JavaScript found; refusing to pass an empty scan.");
  process.exit(2);
}

const files = paths.map((path) => ({
  path: relative(process.cwd(), path),
  content: readFileSync(path, "utf8"),
}));
const secretValues = {
  ...Object.fromEntries(SECRET_ENV_NAMES.map((name) => [name, process.env[name]])),
  ...publicSecretValues(process.env),
};
const findings = findSecretsInBundle(files, secretValues);

if (findings.length > 0) {
  console.error(`bundle scan: ${findings.length} finding(s) in browser-served files:`);
  for (const { path, rule, detail } of findings) console.error(`  ${path}: ${rule} (${detail})`);
  process.exit(1);
}
console.log(`bundle scan: ${files.length} browser-served files, no server secrets.`);
