#!/usr/bin/env sh
# Runs the API test suite the way CI does, fast and safely:
#   - CI-like settings only: no .env is read, so no real key (Gemini, Stripe) and no
#     remote database can reach a test; live tests skip (no GEMINI_API_KEY).
#   - A local Postgres only (default: `docker compose up -d` at the repo root). A remote
#     TEST_DATABASE_URL is refused unless TEST_DATABASE_ALLOW_REMOTE=1.
#   - Schema migrated first (`alembic upgrade head`), then tests spread over every CPU
#     core with pytest-xdist (one worker per test file, so a file's tests stay in order).
#
#   sh scripts/test.sh                 # from apps/api (the pre-push hook runs this)
#   sh scripts/test.sh -k usage -n 0   # extra args go to pytest (-n 0: no parallelism)
set -eu
cd "$(dirname "$0")/.."

url="${TEST_DATABASE_URL:-postgresql://tether:tether@localhost:5432/tether}"
# scheme://[user[:password]@]host[:port]/db -> host
rest="${url#*://}"
rest="${rest##*@}"
hostport="${rest%%/*}"
host="${hostport%:*}"
case "$host" in
  localhost | 127.0.0.1 | "[::1]") ;;
  *)
    if [ "${TEST_DATABASE_ALLOW_REMOTE:-}" != "1" ]; then
      echo "api tests: refusing remote database host '$host'. Use a local Postgres" >&2
      echo "  (docker compose up -d), or set TEST_DATABASE_ALLOW_REMOTE=1 for a disposable one." >&2
      exit 1
    fi
    ;;
esac

# The same placeholders CI uses (.github/workflows/api.yml). Nothing here is a secret.
unset GEMINI_API_KEY CLERK_AUTHORIZED_PARTIES
export TETHER_ENV_FILE=""
export DATABASE_URL="$url"
export ENVIRONMENT=ci APP_VERSION=ci
export CORS_ORIGINS=https://example.com APP_BASE_URL=https://example.com
export CLERK_ISSUER=https://example.clerk.accounts.dev
export STRIPE_SECRET_KEY=sk_test_ci_placeholder
export STRIPE_WEBHOOK_SECRET=whsec_ci_placeholder
export STRIPE_PRICE_ID=price_ci_placeholder

if ! migrate_log=$(uv run alembic upgrade head 2>&1); then
  echo "$migrate_log" >&2
  echo "api tests: could not migrate the test database at $host. Is Postgres running?" >&2
  echo "  docker compose up -d   (from the repo root)" >&2
  exit 1
fi

exec uv run pytest -q -n auto --dist loadfile "$@"
