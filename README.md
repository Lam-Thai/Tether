# Tether

**An AI receptionist for small businesses.** Tether answers the phone and the website chat, answers customers' questions, books real appointments into the owner's calendar, and hands anything tricky to a human, so the owner never has to choose between doing their job and picking up the phone.

> Think of it as a friendly front-desk person who never sleeps, never forgets to write things down, and always says "let me get the owner for you" when they're not sure.

---

## Contents

1. [The problem](#the-problem)
2. [What Tether does](#what-tether-does)
3. [How a phone call works, step by step](#how-a-phone-call-works-step-by-step)
4. [What the business owner gets](#what-the-business-owner-gets)
5. [The promises Tether keeps](#the-promises-tether-keeps)
6. [The technology, in plain words](#the-technology-in-plain-words)
7. [How the code is organised](#how-the-code-is-organised)
8. [How it's built: AI helpers, skills and subagents](#how-its-built-ai-helpers-skills-and-subagents)
9. [How we check it works](#how-we-check-it-works)
10. [Cost](#cost)
11. [For developers: getting started](#for-developers-getting-started)

---

## The problem

A hairdresser is in the middle of a cut. A plumber is under a sink. A spa therapist is with a client. The phone rings, and nobody can answer it. That caller often just books with someone else.

Missed calls are lost customers. Hiring a receptionist is expensive, and most voicemail goes unheard until the end of the day.

## What Tether does

Each business gets its own phone number and its own AI receptionist. When a customer calls or types in the chat, Tether:

- **Answers common questions** ("Are you open Sunday?", "How much is a beard trim?") using the information the owner gave it.
- **Books appointments** by checking the owner's real Google Calendar for a free slot and adding the booking there.
- **Escalates** anything it can't or shouldn't handle (a complaint, an emergency, something it isn't sure about) by alerting the owner with a text message.
- **Writes everything down**: a full transcript and a short two-sentence summary of every conversation, so the owner can catch up in seconds.

It's built first for appointment-based businesses: salons, barbershops, spas, trades and repair services.

## How a phone call works, step by step

Here's what happens when someone calls a salon that uses Tether:

1. **The customer dials the salon's Tether number.** Twilio (a phone company for software) picks up the call.
2. **Tether greets them** in a natural voice, using the greeting and personality the owner chose.
3. **The customer says what they want**, for example *"Can I get a haircut Friday afternoon?"* Their speech is turned into text.
4. **Gemini (Google's AI) reads the text and works out what the customer wants**: a booking, a haircut, Friday afternoon.
5. **Gemini doesn't act on its own. It asks Tether's backend to do it.** The backend is the part of Tether that holds the rules. It checks the request makes sense (is that a real service? is the salon open then?) and then asks Google Calendar for a free slot.
6. **Only when the calendar confirms the booking does Tether tell the customer it's booked.** If something fails, the customer hears an honest "I couldn't do that, someone will call you back", never a fake "you're all set".
7. **The conversation is saved**: transcript, summary and any booking. If a human needs to step in, the owner gets a text.

The short version, in one line each:

| Part | Its job |
|---|---|
| **Gemini** | Understands what the customer means. |
| **The backend (FastAPI)** | Checks the request against the rules and decides what's allowed. |
| **The database (PostgreSQL)** | Remembers everything: businesses, conversations, bookings. |
| **Google Calendar** | The source of truth for when the owner is actually free. |
| **Twilio** | Carries the phone call and sends text messages. |

## What the business owner gets

Owners manage everything from a website dashboard. No coding needed.

- **Onboarding**: sign up, name the business, pick a free trial or a plan.
- **Agent Builder**: choose the receptionist's greeting, tone and voice; add services, prices, opening hours and FAQs; set booking rules. There's a test chat to try it out before going live.
- **Phone**: activate a phone number for the receptionist.
- **Calendar**: connect Google Calendar with one click.
- **Inbox**: every call and chat, with its transcript, summary, and any booking or escalation.
- **Usage**: how much of the monthly AI and phone allowance has been used.
- **Settings**: integrations, data export and deletion, billing.

There's also a public landing page that explains the product, and privacy and terms pages.

## The promises Tether keeps

These are hard rules built into the code, not just good intentions:

- **No fake confirmations.** Tether never tells a caller "you're booked" or "I've sent you a text" unless the real system confirmed it.
- **The AI never acts directly.** Gemini can only *suggest* an action. The backend checks every suggestion before anything happens.
- **Each business only sees its own data.** One salon can never see another salon's customers, even by accident.
- **Usage is capped.** Every request Tether makes to an outside service (AI, phone, calendar) is counted against a limit. When the limit is reached, Tether stops making those requests and gives a polite fallback instead. (A phone call that arrives after the limit still gets a short spoken message before Tether hangs up, so on a paid phone plan that call would still cost a little.)
- **No secrets in the browser.** Passwords and keys for outside services live only on the server.
- **Only made-up data in development.** Test customers are fictional people (like "Alex Rivera"), never real ones.

## The technology, in plain words

| Tool | What it is | What Tether uses it for |
|---|---|---|
| **Next.js + TypeScript** | A popular toolkit for building websites. | The landing page and the owner's dashboard. |
| **Tailwind CSS + shadcn/ui** | Ready-made styling and building blocks (buttons, forms, menus). | Making the website look consistent. |
| **React Hook Form + Zod** | Helpers for web forms. | Checking form entries (like a phone number) before they're sent. |
| **Clerk** | A sign-in service. | Owner accounts, sign-up and login. |
| **FastAPI (Python)** | A framework for building the "brain" behind a website. | The backend: all the rules, checks and decisions. |
| **PostgreSQL** | A database: a very organised filing cabinet. | Storing businesses, conversations, bookings and usage. |
| **Google Gemini** | Google's AI model. | Understanding customers, writing replies and summaries, and speaking with a natural voice. |
| **Twilio** | A phone and text-message service for software. | Answering calls, hearing what callers say, and sending SMS alerts. |
| **Google Calendar** | The calendar many owners already use. | Checking free time and adding bookings. |
| **Stripe** | An online payments service. | The owner's monthly Tether subscription (Tether doesn't take payments from customers). |
| **Vercel + Neon** | Hosting services for websites and databases. | Running Tether on the internet. |
| **GitHub** | Where the code lives. | Storing code, tracking tasks, and running automatic checks. |

The look of the website follows its own design system, called **Construtivismo Dinâmico Tech**, described in [`apps/web/DESIGN.md`](apps/web/DESIGN.md).

## How the code is organised

The project is split into two apps that live side by side:

- **[`apps/web`](apps/web)** is the **website**: what owners and visitors see and click.
- **[`apps/api`](apps/api)** is the **backend**: the part that thinks, checks and remembers. The website talks to it, Twilio talks to it, and it talks to Gemini, Google Calendar and Stripe.

Inside the backend, the code is stacked in layers, like floors in a building. Each floor only talks to the one directly below it:

| Layer | Everyday comparison |
|---|---|
| `api` | The reception desk: takes requests in and hands answers back. |
| `services` | The managers: make the actual decisions and apply the rules. |
| `repositories` | The filing clerks: the only ones allowed to open the filing cabinet (database), and only the drawer for the right business. |
| `models` | The labels on the folders: what's stored and how. |
| `integrations` | The phone lines to the outside world: Gemini, Twilio, Google Calendar, Stripe, Clerk. Each one has a pretend version used in tests. |
| `core` | Building rules that apply everywhere: settings, security, spending limits. |

Other useful places:

- [`PRD.md`](PRD.md): the full product description: what Tether should do and why.
- [`CLAUDE.md`](CLAUDE.md): the rules and conventions for anyone (human or AI) writing code here.
- [`docs/adr`](docs/adr): short write-ups of big decisions and why they were made (design system, billing, calendar access, call audio).
- [`docs/runbook-live-demo.md`](docs/runbook-live-demo.md): a step-by-step script for testing a real call end to end.

## How it's built: AI helpers, skills and subagents

Tether is built with the help of **Claude Code**, an AI coding assistant. To keep that assistant consistent and careful, the project gives it two kinds of instructions: **skills** and **subagents**.

- A **skill** is a **recipe card**: a checklist for one specific kind of job. When the assistant does that job, it pulls out the card and follows the steps, so the job is done the same safe way every time.
- A **subagent** is a **specialist teammate**: a separate AI helper with a narrow role, its own instructions and its own tools. The main assistant can hand a focused task to a specialist and get the result back.

### The work happens in "slices"

Every feature is built as a **slice**: one thin piece that goes all the way through, from the button on the website, through the backend, down to the database or outside service. Like cutting a slice of layered cake instead of baking one layer at a time. This way every piece of work is something you can actually try out.

### Skills (recipe cards)

These live in [`.claude/skills`](.claude/skills):

| Skill | When it's used | What it makes sure of |
|---|---|---|
| **tracer-slice** | Before starting any new feature. | Plans the slice so it really goes from top to bottom, and names the main piece of logic it's built around. |
| **web-feature** | Adding a page or section to the website. | The page follows the design system and talks to the backend the approved way. |
| **api-endpoint** | Adding or changing a backend address the website calls. | Each request is tied to the signed-in business and checked properly. |
| **db-migration** | Changing what the database stores. | Every change can be undone safely, and old data isn't lost. |
| **gemini-tool** | Giving the AI a new ability (like "check availability" or "create booking"). | The AI can only *ask* for the action; the backend checks and performs it. |
| **provider-adapter** | Connecting to a new outside service, or changing one. | Every call is counted against the spending limit and retried only a few times. |
| **webhook-handler** | Handling messages that outside services send *to* Tether (like Twilio saying "a call just came in"). | The message is genuine, and handling the same message twice doesn't cause a double booking. |

### Subagents (specialist teammates)

These live in [`.claude/agents`](.claude/agents):

| Subagent | Their specialty |
|---|---|
| **frontend-engineer** | The website: pages, forms, sign-in, the landing page and its scroll animations. |
| **backend-engineer** | The backend: its addresses, rules, database tables and database changes. |
| **ai-engineer** | Everything about Gemini: how it's instructed, the actions it may request, its safety behaviour, and the test exam it's graded on. |
| **test-engineer** | Writing extra tests for work that's already done, especially "what if it fails?" cases and checks that businesses can't see each other's data. |
| **code-reviewer** | A read-only reviewer that checks changes against Tether's rules before they're merged: data separation, no fake confirmations, no leaked secrets, safe retries, undoable database changes. |

### Outside skill packs

The project also uses two shared collections of skills:

- **mattpocock-skills**: general good habits for writing software, such as writing the test first, debugging methodically, and reviewing code.
- **taste-skill**: a design "quality gate" that runs before any visual change, to stop the website from looking generic.

## How we check it works

- **Automatic tests** for both apps. They use pretend versions of Gemini, Twilio, Google Calendar and Stripe, so they're fast, free and never touch real customers.
- **A check before every upload.** Pushing code to GitHub first runs the website tests and the backend tests (about 40 seconds). If either fails, the upload is stopped. The real-browser click-through runs separately, on GitHub.
- **Checks on every proposed change** on GitHub: tests, code style, type checks, a full click-through of the main journey in a real browser, database changes tested forwards and backwards, a scan for leaked passwords, and a scan for known security problems in the tools we depend on.
- **An AI exam.** A set of sample conversations (the "eval harness") that grades how well the real Gemini answers questions, books, and escalates. It's run by hand because it uses real AI requests.
- **A live demo script** ([`docs/runbook-live-demo.md`](docs/runbook-live-demo.md)) for making a real test call end to end.

## Cost

Tether is built as a working prototype on **free tiers only**: Gemini's free allowance, a Twilio trial account, Stripe test mode, Clerk's development plan, and free hosting. Usage limits are built into the product to keep it inside those free allowances. They limit what Tether itself does, not everything a provider could charge for, which is why development sticks to trial and test accounts.

---

## For developers: getting started

Requirements:

- Node.js 24 LTS or newer (`.nvmrc` pins 24)
- pnpm 12 (`npm i -g pnpm@12`; the exact version is pinned in `package.json` → `packageManager`)
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) for `apps/api`
- Docker (for a local Postgres via `docker compose`), or a [Neon](https://neon.tech) dev branch

```sh
pnpm install              # installs JS/TS dependencies and the Husky git hooks
docker compose up --wait  # starts local Postgres and waits for its healthcheck (apps/api/.env's DATABASE_URL points here by default)
cp apps/api/.env.example apps/api/.env      # fill in DATABASE_URL etc.
cp apps/web/.env.example apps/web/.env.local
uv sync --directory apps/api               # installs Python dependencies
uv run --directory apps/api alembic upgrade head   # apply all migrations

pnpm dev       # runs apps/web (Next.js) and apps/api (uvicorn --reload) together
```

Open http://localhost:3000 for the landing page, and http://localhost:3000/status for `API: healthy · Database: connected`.

On Windows, point `DATABASE_URL` at `127.0.0.1`, not `localhost` (see [`CLAUDE.md`](CLAUDE.md)).

### Billing (Stripe test mode)

`apps/api` won't start without the Stripe settings (see `apps/api/.env.example`). Use **test-mode** keys only; a live key is rejected unless `ENVIRONMENT=production`.

1. In the Stripe Dashboard (test mode), copy the secret key into `STRIPE_SECRET_KEY`, and create a recurring monthly price; put its id in `STRIPE_PRICE_ID`.
2. Set `APP_BASE_URL=http://localhost:3000`.
3. Forward webhooks to the local API and copy the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET`:

   ```sh
   stripe login
   stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
   ```

Pay with test card `4242 4242 4242 4242` (any future date, any CVC). The free trial needs no card.

### AI receptionist (Gemini)

The test chat at `/dashboard/agent` calls real Gemini through `apps/api`. Create a key in [Google AI Studio](https://aistudio.google.com/apikey) and set `GEMINI_API_KEY` in `apps/api/.env` (see `apps/api/.env.example` for the model and timeout settings). Without a key the API still starts locally, but every reply is the "having trouble responding" fallback. Keep `ENVIRONMENT=local` locally; `production` requires the key and is only for the deployed API.

Optional live smoke test against real Gemini:

```sh
uv run --directory apps/api pytest -m live
```

The AI eval harness (costs real Gemini requests; never part of CI):

```sh
uv run --directory apps/api tether-eval --suite core
```

### Tests and checks

```sh
pnpm test                       # Vitest across all projects (tooling + apps/web)
pnpm typecheck                  # strict TypeScript check of root tooling
sh apps/api/scripts/test.sh     # API suite with CI-like settings (needs local Postgres)
pnpm e2e                        # Playwright critical journey (needs local Postgres and E2E_CLERK_* env vars)
```

E2E always runs against the local Postgres (`docker compose up --wait`), never the Neon `DATABASE_URL`. See [E2E setup](apps/web/CLAUDE.md#e2e-slice-17) for the Clerk test user and browser install.

See the root [`CLAUDE.md`](CLAUDE.md) for the full command reference (per-app lint/typecheck/test/build commands).

### Pre-push hook

`git push` runs the Husky `pre-push` hook (`.husky/pre-push`) and is rejected if any check fails:

1. `pnpm test`, the Vitest suite.
2. At the same time, `apps/api/scripts/test.sh`: the API suite with CI-like settings (your `.env` is not read, so no real key or remote database reaches a test), migrations, then pytest in parallel. It needs the local Postgres (`docker compose up -d`, which on Windows needs WSL) and refuses a remote `TEST_DATABASE_URL` unless `TEST_DATABASE_ALLOW_REMOTE=1`.

Both halves together take about 40 seconds.

The suite includes `tooling/repo-hygiene.test.ts`, which fails if any env file other than `.env.example` is tracked (`.env*`, `.envrc`, `*.env`).

If a git GUI client reports `pnpm: command not found` from the hook, it isn't loading your shell's PATH (common with nvm/fnm). Add the PATH setup to `~/.config/husky/init.sh`, which Husky sources before every hook.

In a real emergency you can skip the hook with `git push --no-verify`. CI still has to pass before merge.

### Deployment

CI (`.github/workflows/api.yml`, `.github/workflows/web.yml`) lints, type-checks, tests, and builds both apps on every PR. Actually deploying is a one-time manual setup:

1. **Neon**: create a project, grab the pooled connection string (`postgresql://...`). You'll set this as `DATABASE_URL` in the API's Vercel project. The backend normalizes it to the `asyncpg` driver automatically.
2. **Vercel — API** (`apps/api`): new project, Root Directory = `apps/api`. Vercel auto-detects the FastAPI app from `main.py` (no `vercel.json` needed). Env vars: `DATABASE_URL` (from Neon), `CORS_ORIGINS` (the web app's deployed URL, once known), `ENVIRONMENT=production`, `APP_VERSION` (e.g. a git SHA or release tag).
3. **Vercel — Web** (`apps/web`): new project, Root Directory = `apps/web`. Env vars: `NEXT_PUBLIC_API_BASE_URL` (the API project's deployed URL from step 2).
4. Go back and update the API project's `CORS_ORIGINS` with the web app's actual deployed URL from step 3, then redeploy the API.
5. Run `uv run --directory apps/api alembic upgrade head` against the Neon database once (locally, with `DATABASE_URL` pointed at Neon) before relying on it. Migrations are never run automatically on startup or in CI against Neon.

Once both are deployed, the web URL should show `API: healthy · Database: connected`.
