// .env, .env.local, .env-prod, .env., .envrc; case-insensitive for case-insensitive filesystems.
const DOTENV_NAME = /^\.env(rc)?($|[.-])/i;
// prod.env, secrets.env
const ENV_SUFFIX = /\.env$/i;
const ALLOWED_ENV_FILE = ".env.example";

export function findForbiddenEnvFiles(paths: readonly string[]): string[] {
  return paths.filter((p) => {
    const segments = p.split("/");
    const base = segments.pop() ?? "";
    if (segments.some((dir) => dir.toLowerCase() === ".env")) return true;
    if (base === ALLOWED_ENV_FILE) return false;
    return DOTENV_NAME.test(base) || ENV_SUFFIX.test(base);
  });
}
