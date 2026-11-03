#!/usr/bin/env sh
# Starts the API the way the Playwright E2E suite needs it (slice 17):
#   - REAL local Postgres (127.0.0.1) and REAL Clerk dev-instance token verification;
#   - Gemini, Stripe and Google Calendar replaced by in-process fakes (E2E_FAKE_PROVIDERS=1);
#     Twilio, TTS and STT stay unconfigured (SMS alerts are logged, never sent);
#   - no .env file is read (TETHER_ENV_FILE=), so no real key can leak into the run;
#   - rate limits OFF (the browser sends bursts), generous concurrency, and a SMALL
#     per-business Gemini cap so a test can reach the quota fallback.
#
#   E2E_CLERK_ISSUER=https://<slug>.clerk.accounts.dev sh scripts/e2e-server.sh
#
# Overridable: E2E_DATABASE_URL (must be local), E2E_PORT (8000), E2E_WEB_ORIGIN
# (http://localhost:3000), E2E_GEMINI_REQUESTS_PER_BUSINESS_PER_DAY (14), E2E_CLERK_AUTHORIZED_PARTIES.
# Reset the test user between runs: uv run python -m seeds.e2e_reset --email <addr>
set -eu
cd "$(dirname "$0")/.."

issuer="${E2E_CLERK_ISSUER:-${CLERK_ISSUER:-}}"
if [ -z "$issuer" ]; then
  echo "e2e-server: set E2E_CLERK_ISSUER (or CLERK_ISSUER) to the Clerk dev instance issuer." >&2
  exit 1
fi
web="${E2E_WEB_ORIGIN:-http://localhost:3000}"

export TETHER_ENV_FILE=""
export ENVIRONMENT=ci APP_VERSION=e2e
export E2E_FAKE_PROVIDERS=1
export DATABASE_URL="${E2E_DATABASE_URL:-postgresql://tether:tether@127.0.0.1:5432/tether}"
export CLERK_ISSUER="$issuer"
export CLERK_AUTHORIZED_PARTIES="${E2E_CLERK_AUTHORIZED_PARTIES:-$web}"
export CORS_ORIGINS="$web" APP_BASE_URL="$web"
# Placeholders only: the Stripe fake never reads them, and settings need the shapes.
export STRIPE_SECRET_KEY=sk_test_e2e_placeholder
export STRIPE_WEBHOOK_SECRET=whsec_e2e_placeholder
export STRIPE_PRICE_ID=price_e2e_placeholder
unset GEMINI_API_KEY CLERK_SECRET_KEY TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_FROM_NUMBER
export RATE_LIMIT_ENABLED=false
export GEMINI_REQUESTS_PER_DAY=100000
export GEMINI_REQUESTS_PER_BUSINESS_PER_DAY="${E2E_GEMINI_REQUESTS_PER_BUSINESS_PER_DAY:-14}"
export MAX_CONCURRENT_INTERACTIONS=1000 MAX_CONCURRENT_INTERACTIONS_PER_BUSINESS=100

# Local databases only: the E2E reset deletes whole businesses.
uv run python -c 'import sys
from core.config import settings
from core.db_hosts import is_local_database
sys.exit(0 if is_local_database(settings.database_url) else 3)' || {
  echo "e2e-server: refusing a database that is not on this machine." >&2
  exit 1
}

uv run alembic upgrade head
exec uv run uvicorn main:app --host 127.0.0.1 --port "${E2E_PORT:-8000}"
