import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { findForbiddenEnvFiles } from "./repo-hygiene.ts";

const repoRoot = path.resolve(import.meta.dirname, "..");

function trackedFiles(): string[] {
  // -z: NUL-separated, so paths are never quoted or escaped by git.
  const out = execFileSync("git", ["ls-files", "-z"], { cwd: repoRoot, encoding: "utf8" });
  return out.split("\0").filter(Boolean);
}

describe("findForbiddenEnvFiles", () => {
  it("flags .env and .env.* files at any depth", () => {
    expect(
      findForbiddenEnvFiles([".env", ".env.local", "apps/api/.env.production", "apps/web/.env.sample"]),
    ).toEqual([".env", ".env.local", "apps/api/.env.production", "apps/web/.env.sample"]);
  });

  it("flags common env-file variants", () => {
    const variants = [".envrc", ".env-prod", ".env.", ".ENV", "apps/api/.Env.Local", "prod.env", "deploy/secrets.env"];
    expect(findForbiddenEnvFiles(variants)).toEqual(variants);
  });

  it("flags anything inside a .env directory", () => {
    expect(findForbiddenEnvFiles([".env/prod", "apps/api/.env/keys.json"])).toEqual([".env/prod", "apps/api/.env/keys.json"]);
  });

  it("allows .env.example at any depth", () => {
    expect(findForbiddenEnvFiles([".env.example", "apps/api/.env.example"])).toEqual([]);
  });

  it("ignores files that only look similar", () => {
    expect(
      findForbiddenEnvFiles(["src/env.ts", "src/environment.ts", ".envoy.yaml", "docs/env.md", "README.md"]),
    ).toEqual([]);
  });
});

describe("repository hygiene", () => {
  it("tracks no .env files except .env.example", () => {
    expect(findForbiddenEnvFiles(trackedFiles())).toEqual([]);
  });

  it("enables taste-skill as a project-scoped Claude Code plugin", () => {
    const settings = JSON.parse(readFileSync(path.join(repoRoot, ".claude/settings.json"), "utf8"));

    expect(settings.enabledPlugins?.["taste-skill@taste-skill"]).toBe(true);
    expect(settings.extraKnownMarketplaces?.["taste-skill"]?.source).toEqual({
      source: "github",
      repo: "leonxlnx/taste-skill",
    });
  });
});
