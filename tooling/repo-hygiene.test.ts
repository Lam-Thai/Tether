import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { findDesignViolations, findForbiddenEnvFiles } from "./repo-hygiene.ts";

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

describe("findDesignViolations", () => {
  const scan = (content: string, file = "apps/web/components/x.tsx") =>
    findDesignViolations([{ path: file, content }]).map((v) => v.rule);

  it("flags h-screen in any form", () => {
    expect(scan('<main className="h-screen" />')).toEqual(["h-screen"]);
    expect(scan('<main className="min-h-screen p-8" />')).toEqual(["h-screen"]);
  });

  it("flags 100vh in arbitrary values and raw CSS", () => {
    expect(scan('<main className="h-[100vh]" />')).toEqual(["h-screen"]);
    expect(scan("height: 100vh;", "a.css")).toEqual(["h-screen"]);
  });

  it("allows the dvh replacement", () => {
    expect(scan('<main className="min-h-[100dvh]" />')).toEqual([]);
  });

  it("flags pure black as hex, utility or rgb()", () => {
    for (const black of ["#000000", "#000", "#000f", "#000000ff"]) {
      expect(scan(`color: ${black};`, "a.css")).toEqual(["pure-black"]);
    }
    expect(scan('<p className="text-black bg-black/50" />')).toEqual(["pure-black"]);
    expect(scan("color: rgb(0 0 0 / 0.5);", "a.css")).toEqual(["pure-black"]);
  });

  it("does not mistake other hex colors for pure black", () => {
    expect(scan("color: #0000ff; background: #000080; border: #111111;", "a.css")).toEqual([]);
    expect(scan('<p className="text-blackish rgb(17 17 17)" />')).toEqual([]);
  });

  it("flags Tailwind and CSS gradients", () => {
    expect(scan('<div className="bg-gradient-to-r from-red-500" />')).toEqual(["gradient"]);
    expect(scan('<div className="bg-linear-to-r from-red-500" />')).toEqual(["gradient"]);
    expect(scan('<div className="bg-radial from-red-500" />')).toEqual(["gradient"]);
    expect(scan("background: linear-gradient(red, blue);", "a.css")).toEqual(["gradient"]);
    expect(scan("background: radial-gradient(circle, red, blue);", "a.css")).toEqual(["gradient"]);
  });

  it("flags emoji in tsx but not text-default symbols", () => {
    expect(scan("<p>Booked \u{1F389}</p>")).toEqual(["emoji"]);
    expect(scan("<p>© 2026 Tether™</p>")).toEqual([]);
  });

  it("reports the line number of each violation", () => {
    expect(findDesignViolations([{ path: "a.css", content: "ok\ncolor: #000;" }])).toEqual([
      { path: "a.css", line: 2, rule: "pure-black" },
    ]);
  });
});

function webSourceFiles(): { path: string; content: string }[] {
  return ["apps/web/app", "apps/web/components"].flatMap((dir) =>
    readdirSync(path.join(repoRoot, dir), { recursive: true, encoding: "utf8" })
      .filter((file) => /\.(tsx?|css)$/.test(file))
      .map((file) => {
        const relative = path.posix.join(dir, file.split(path.sep).join("/"));
        return { path: relative, content: readFileSync(path.join(repoRoot, relative), "utf8") };
      }),
  );
}

describe("repository hygiene", () => {
  it("keeps apps/web free of DESIGN.md banned patterns", () => {
    const files = webSourceFiles();
    expect(files.some((f) => f.path === "apps/web/app/globals.css")).toBe(true);
    expect(findDesignViolations(files)).toEqual([]);
  });

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
