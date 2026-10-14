const ENV_FILE = /^\.env(\..+)?$/;
const ALLOWED_ENV_FILE = ".env.example";

export function findForbiddenEnvFiles(paths: readonly string[]): string[] {
  return paths.filter((p) => {
    const base = p.slice(p.lastIndexOf("/") + 1);
    return ENV_FILE.test(base) && base !== ALLOWED_ENV_FILE;
  });
}
