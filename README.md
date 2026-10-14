# Tether

AI receptionist for local service businesses. See `PRD.md` for the product and `CLAUDE.md` for engineering conventions.

## Getting started

Requirements:

- Node.js 24 LTS or newer (`.nvmrc` pins 24)
- pnpm 12 (`npm i -g pnpm@12`; the exact version is pinned in `package.json` → `packageManager`)

```sh
pnpm install   # installs dependencies and the Husky git hooks
pnpm test      # runs Vitest across all projects
pnpm typecheck # strict TypeScript check of root tooling
```

### Pre-push hook

`git push` runs the Husky `pre-push` hook (`.husky/pre-push`) and is rejected if any check fails:

1. `pnpm test`, the Vitest suite.
2. `uv run pytest` in `apps/api`. This runs only once `apps/api/pyproject.toml` exists; until then the step is skipped.

The suite includes `tooling/repo-hygiene.test.ts`, which fails if any env file other than `.env.example` is tracked (`.env*`, `.envrc`, `*.env`).

If a git GUI client reports `pnpm: command not found` from the hook, it isn't loading your shell's PATH (common with nvm/fnm). Add the PATH setup to `~/.config/husky/init.sh`, which Husky sources before every hook.

In a real emergency you can skip the hook with `git push --no-verify`. CI still has to pass before merge.
