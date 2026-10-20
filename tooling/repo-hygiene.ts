// .env, .env.local, .env-prod, .env., .envrc; case-insensitive for case-insensitive filesystems.
const DOTENV_NAME = /^\.env(rc)?($|[.-])/i;
// prod.env, secrets.env
const ENV_SUFFIX = /\.env$/i;
const ALLOWED_ENV_FILE = ".env.example";

export interface DesignViolation {
  path: string;
  line: number;
  rule: "h-screen" | "pure-black" | "gradient" | "emoji";
}

// apps/web/DESIGN.md bans. `\bh-screen` also catches min-/max-h-screen; 100vh
// catches h-[100vh] and raw CSS. Gradients cover Tailwind v3 and v4 names.
const DESIGN_RULES: ReadonlyArray<{ rule: DesignViolation["rule"]; pattern: RegExp }> = [
  { rule: "h-screen", pattern: /\bh-screen\b|\b100vh\b/ },
  {
    rule: "pure-black",
    pattern:
      /#000(?:[0-9a-f]|000(?:[0-9a-f]{2})?)?\b|\b(?:bg|text|border|ring|outline|fill|stroke|from|via|to|shadow)-black\b|\brgba?\(\s*0[\s,]+0[\s,]+0\b/i,
  },
  {
    rule: "gradient",
    pattern: /\bbg-gradient-|\bbg-(?:linear|radial|conic)\b|\b(?:linear|radial|conic)-gradient\(/,
  },
];
// Emoji_Presentation excludes text-default symbols such as (c) and (tm).
const EMOJI = /\p{Emoji_Presentation}|\p{Extended_Pictographic}️/u;

export function findDesignViolations(
  files: ReadonlyArray<{ path: string; content: string }>,
): DesignViolation[] {
  const violations: DesignViolation[] = [];
  for (const { path, content } of files) {
    content.split("\n").forEach((text, index) => {
      for (const { rule, pattern } of DESIGN_RULES) {
        if (pattern.test(text)) violations.push({ path, line: index + 1, rule });
      }
      if (path.endsWith(".tsx") && EMOJI.test(text)) {
        violations.push({ path, line: index + 1, rule: "emoji" });
      }
    });
  }
  return violations;
}

export function findForbiddenEnvFiles(paths: readonly string[]): string[] {
  return paths.filter((p) => {
    const segments = p.split("/");
    const base = segments.pop() ?? "";
    if (segments.some((dir) => dir.toLowerCase() === ".env")) return true;
    if (base === ALLOWED_ENV_FILE) return false;
    return DOTENV_NAME.test(base) || ENV_SUFFIX.test(base);
  });
}
