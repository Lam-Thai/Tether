# Runbook: live demo against real providers (MVP DoD 5-12)

A manual, step-by-step check of PRD §17 Definition of Done items 5-12 against **real** Gemini, Google Calendar and a Twilio **trial** account. Items 1-4 and 13-16 are automated (see the coverage matrix); the optional spot checks in section 5 re-run 14 and 15 against real providers.

- **Who runs it:** the repo owner, signed in as the owner of a test business they created themselves.
- **When:** once before closing slice 17 (#17), and again after any change to the conversation engine, calendar, voice or SMS paths.
- **Time:** about 60-90 minutes the first time (mostly console setup), about 30 minutes after that.
- **Cost:** zero. Free tiers only: the Gemini API free tier, a Google Cloud project with the OAuth app in **Testing** mode, a Twilio **trial** account (no card), a cloudflared quick tunnel or the Vercel Hobby preview, Clerk development and Stripe test mode. Never upgrade, buy a number or add billing to get past a limit. If a limit blocks a step, stop and run that step on another day.
- **Data:** synthetic only. Use the demo salon seed, made-up customer names (Alex Rivera, Sam Lee) and the owner's **own** verified phone. Never type real customer details.
- **Database:** the local docker Postgres, or the Neon **dev** branch. Never production. Get the repo owner's go-ahead before running migrations against Neon.

### Gemini request budget

The free tier gives `gemini-2.5-flash` about **20 requests a day per project** (measured Oct 2026). The TTS model (`gemini-2.5-flash-preview-tts`) has its own small free allowance on the same key. Check today's limits in Google AI Studio before you start. Speech input stays on `VOICE_INPUT_MODE=twilio_stt` (the default), so calls spend no Gemini STT requests.

| Step | DoD | Gemini chat requests | Gemini TTS requests |
|---|---|---|---|
| 1. Test chat: FAQ question | 5 | 2 (tool round + reply) | 0 |
| 2. Availability preview + chat question | 6 | 0 (preview) + 2 (chat) | 0 |
| 3. Book a slot in the chat | 7 | 2 | 0 |
| 4. End chat, read transcript and summary | 9, 10 | 1 (2 if the summary is regenerated) | 0 |
| 5. Escalation chat + SMS, end it | 11, 12 | 1-2 + 1 summary | 0 |
| 6. Voice call (greeting + 2 caller turns + summary) | 8, 9, 10 | 3-4 + 1 summary | 2-3 (the greeting is cached after its first synthesis) |
| Optional: "Preview voice" in the Agent Builder | 8 | 0 | 1 |
| **Total** | | **about 14-17** | **about 3-4** |
| Spot check F1 (calendar revoked) | 15 | 1-2 | 0 |
| Spot check F2 (Tether quota reached) | 14 | 0 (the guard blocks before the call) | 0 |

Run steps 1-6 within one UTC day (Tether's counters reset at 00:00 UTC). Run F1 on another day if steps 1-6 used more than 18 requests. If Google answers 429 mid-run, stop and continue the next day; do not retry in a loop.

---

## 1. Prerequisites

### Accounts and consoles (all free)

- [ ] **Clerk** development instance. Sign-in is email + password only (no extra required fields).
- [ ] **Stripe** test mode, with a test-mode price for `STRIPE_PRICE_ID`. The test business needs a plan (trial) or every business route answers 402. Use Stripe's published test card in Checkout; never a real card.
- [ ] **Gemini** API key from Google AI Studio on a project **without** billing enabled.
- [ ] **Google Cloud** project (free, no billing):
  - Enable the Google Calendar API.
  - OAuth consent screen: External, publishing status **Testing**, your own Google account added as a **test user**, scopes `calendar.calendarlist.readonly`, `calendar.freebusy`, `calendar.events.owned`.
  - OAuth client of type Web application, authorized redirect URI exactly `{APP_BASE_URL}/dashboard/settings/calendar/callback` (locally `http://localhost:3000/dashboard/settings/calendar/callback`).
  - In Google Calendar, create a secondary calendar named `Tether demo (synthetic)`. Bookings go there, and cleanup is deleting it.
  - Testing mode refresh tokens expire after 7 days. The Calendar page then shows **Reconnect needed**: click **Reconnect**.
- [ ] **Twilio trial** (no card):
  - Your own mobile is a **Verified Caller ID** (done at sign-up). A trial can only call and text verified numbers.
  - Claim the free trial phone number in the console. Leave its Voice URL at Twilio's default; Tether only connects numbers that are free.
  - Use the trial account's **live** Account SID and Auth Token. The test credentials (`+15005550006`) send nothing and get no status callbacks, so they cannot prove DoD 12.
- [ ] **Public URL for Twilio webhooks**, one of:
  - Local: `cloudflared tunnel --url http://127.0.0.1:8000` (no account needed). The printed `https://<random>.trycloudflare.com` origin becomes `API_PUBLIC_BASE_URL`. It changes on every run (see "if it fails" in step 6).
  - Vercel Hobby preview of the API: `API_PUBLIC_BASE_URL` is the API deployment's origin. Twilio must reach it without Vercel Authentication; if deployment protection blocks it, use the tunnel instead. Set the API project's Function Max Duration to 30 s in project settings.

### Environment variables (names only; values live in `apps/api/.env`, `apps/web/.env.local` or the Vercel project)

API (`apps/api`):

| Group | Names |
|---|---|
| Core | `DATABASE_URL` (`postgresql://tether:tether@127.0.0.1:5432/tether` locally), `ENVIRONMENT=local`, `APP_BASE_URL`, `CORS_ORIGINS` |
| Clerk | `CLERK_ISSUER`, optional `CLERK_AUTHORIZED_PARTIES`, optional `CLERK_SECRET_KEY` |
| Stripe | `STRIPE_SECRET_KEY` (test key), `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID` |
| Gemini | `GEMINI_API_KEY`; leave `GEMINI_MODEL`, `GEMINI_TTS_MODEL`, `TTS_VOICES`, `TTS_DEFAULT_VOICE` at their defaults |
| Google Calendar | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `CALENDAR_TOKEN_KEYS`, `OAUTH_STATE_SECRET` (generate the last two with `uv run python -m core.keygen`) |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` (the trial number, E.164), `API_PUBLIC_BASE_URL` |
| Voice | `VOICE_INPUT_MODE` unset (= `twilio_stt`) |
| Limits (recommended for the run) | `GEMINI_REQUESTS_PER_DAY=20` so Tether's own quota fallback fires before Google's 429 |
| Vercel only | `TRUSTED_CLIENT_IP_HEADER=x-real-ip` (otherwise every webhook caller shares one rate-limit budget) |

Web (`apps/web`): `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`. No other secret ever goes in the web app.

### Start the stack (local)

1. `docker compose up -d` at the repo root.
2. `cd apps/api && uv sync && uv run alembic upgrade head`. On Neon dev: only after the owner says yes.
3. Start the tunnel (above), put its origin in `API_PUBLIC_BASE_URL`.
4. Run the API on its own so you can keep its JSON log as evidence:
   `uv run uvicorn main:app --port 8000 2>&1 | tee ~/tether-evidence/api-log.jsonl`
   Every `tether.*` line is one JSON object (`ts`, `level`, `logger`, `msg`, `request_id`, `business_id`, `interaction_id`, extras). Phone numbers are masked to `***1234` and message bodies are `[redacted]` by design.
5. `pnpm --filter web dev`, open `http://localhost:3000`.
6. Optional: `stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe` (the return page also syncs the plan without it).

### Set up the test business

1. Sign up, complete `/onboarding`, start the trial on `/billing/plan` (Stripe test mode).
2. Seed the synthetic salon into your business: `cd apps/api && uv run python -m seeds.demo_salon --owner-email <your sign-up email>`. This adds 14 active FAQs, 7 active services (Haircut 45 min / 40.00, Kids cut, Beard trim, ...) and hours (closed Sunday and Monday, Tue-Fri 09:00-18:00 with Thursday to 20:00, Saturday 10:00-16:00) in your business's timezone.
3. `/dashboard/agent#persona`: set the receptionist name and greeting, pick a Gemini voice (not "standard"), Save.
4. `/dashboard/settings/calendar`: **Connect Google Calendar** → Google's consent ("Google hasn't verified this app" is expected in Testing mode; continue) → **Calendar for bookings** = `Tether demo (synthetic)` → **Save calendar**. Expect `Connected · "Tether demo (synthetic)"`.
5. `/dashboard/agent#escalation` (**Escalation rules**): turn on **Text me when a customer needs a person**, **Contact phone** = your verified number, **Save escalation rules**. There must be no "Demo mode" callout (that would mean Twilio is not configured).
6. `/dashboard/phone`: under **Numbers on your Twilio account**, **Connect** the trial number. Expect `Connected · webhooks verified`. Click **Test** to re-verify.

For DB checks below, open psql and set your business id once:

```sh
docker compose exec db psql -U tether -d tether
-- inside psql:
select b.id, b.name, b.timezone from businesses b join users u on u.id = b.owner_id where u.email = '<your sign-up email>';
\set biz '<that id>'
```

### Evidence

Keep evidence **outside the repo** (for example `~/tether-evidence/`; `evidence/` below is relative to it) and attach it to the slice-17 PR or issue #17. Blur your own phone number and email in screenshots and recordings before attaching. Never commit evidence.

---

## 2. DoD coverage matrix

| # | DoD item (PRD §17) | How it is demonstrated |
|---|---|---|
| 1 | Create and configure a test business | Automated: `apps/api/tests/api/test_business.py`, `test_me.py`, `test_billing.py`; Playwright journey "onboarding" in `apps/web/e2e` (lands in PR 3/3). Also done by hand in "Set up the test business". |
| 2 | Define the receptionist's persona | Automated: `tests/api/test_agent.py`; `apps/web/components/agent/agent-builder.test.tsx`; E2E "configure agent" (`apps/web/e2e`, PR 3/3). |
| 3 | Create and edit FAQs | Automated: `tests/api/test_faqs.py`; `components/agent/faq-manager.test.tsx`; E2E "configure FAQ" (PR 3/3). |
| 4 | Configure bookable services | Automated: `tests/api/test_services.py`; `components/agent/services-manager.test.tsx`; E2E "configure service" (PR 3/3). |
| 5 | Gemini interprets a real test request | Manual: **step 1**. Fake-provider version: `tests/services/test_conversation.py`, E2E "test-chat FAQ" (PR 3/3). Opt-in live: `tests/integrations/test_gemini_live.py`; evals `uv run tether-eval`. |
| 6 | Backend returns available Google Calendar slots | Manual: **step 2**. Fake: `tests/api/test_calendar_availability.py`, `tests/services/test_availability_slots.py`. |
| 7 | Create an appointment and verify the result | Manual: **step 3**. Fake: `tests/api/test_bookings.py`, `tests/services/test_booking_concurrency.py`, E2E "booking" (PR 3/3). |
| 8 | Voice flow plays a Gemini-generated response | Manual: **step 6**. Fake: `tests/api/test_voice_tts.py` (`<Play>` over signed webhooks). |
| 9 | System records an interaction transcript | Automated: `tests/api/test_inbox.py`, `tests/api/test_voice.py`; E2E "inbox review" (PR 3/3). Manual (real providers): **steps 4 and 6**. |
| 10 | Gemini produces a two-sentence summary | Manual: **step 4** (and 6). Fake: `tests/services/test_summarizer.py`; E2E "summary visible" (PR 3/3); eval suite `summary`. |
| 11 | Escalation creates an alert record | Manual: **step 5**. Fake: `tests/api/test_escalations.py`, `tests/services/test_escalation_policy.py`; E2E "escalation" (PR 3/3). |
| 12 | Test SMS sent within provider restrictions | Manual: **step 5**. Fake: `tests/api/test_twilio_webhooks.py` (escalated chat → one SMS → signed callbacks → `delivered`); opt-in `tests/integrations/test_twilio_live.py` (test credentials). |
| 13 | Owner reviews the interaction in the dashboard | Automated: `tests/api/test_inbox.py`, `apps/web/components/inbox/*.test.tsx`; E2E "inbox review" (PR 3/3). Seen by hand in steps 4-6. |
| 14 | Backend usage limits work | Automated: `tests/services/test_usage.py`, `tests/repositories/test_usage.py`, `tests/api/test_usage.py`, `tests/api/test_rate_limit.py`; E2E "usage limit fallback" (PR 3/3). Optional live: **F2**. |
| 15 | AI and calendar failures handled safely | Automated: `tests/chaos/` (`test_provider_failures.py`, `test_voice_failures.py`, `test_chaos_mapping.py`). Optional live: **F1**. |
| 16 | Workspaces cannot access each other's data | Automated: `tests/security/test_tenant_isolation_sweep.py` (+ `test_isolation_sweep_detects_leaks.py`). |

All API tests run in the **API** workflow job "Lint, type-check, test, migrate" (which also runs migrations up → down → up and `alembic check`) and in the `git push` hook. The **Security** workflow adds gitleaks, `pnpm audit` and `pip-audit`; the **Web** workflow scans the client bundle for server secrets.

---

## 3. Manual steps

Before step 1, open `/dashboard/settings/usage` and note today's `gemini_request` and `tts_request` counts. Screenshot it as `evidence/usage-before.png`.

### Step 1 (DoD 5): Gemini interprets a real request

- **Preconditions:** setup done; `GEMINI_API_KEY` set.
- **Do:** `/dashboard/agent#test-chat` (**Test your receptionist**). Send: `Hi, do you take walk-ins on Saturday?`
- **Expect:** a reply that matches the seeded FAQ (walk-ins before 3 PM, Monday to Saturday), not marked as a fallback.
- **Verify in the backend:**
  - `/dashboard/settings/usage`: `gemini_request` went up by about 2.
  - psql: `select tool_name, status, result from interaction_operations where interaction_id = (select id from interactions where business_id = :'biz' order by started_at desc limit 1);` shows `search_faq` `succeeded`.
  - Log: no `"msg": "model turn failed"` line on `tether.conversation` for this `interaction_id`.
- **Evidence:** `evidence/dod-05-gemini-chat.png` (chat + reply).
- **If it fails:** a fallback reply means the model call or validation failed. Check `tether.conversation` for the reason code. A 429 from Google means today's free quota is gone: stop and resume tomorrow.

### Step 2 (DoD 6): real free/busy slots

- **Preconditions:** calendar `Connected`. In Google Calendar, add a synthetic busy event `Runbook block` on `Tether demo (synthetic)` for the next Tuesday-Friday (at least 2 hours from now), 14:00-15:00 business time.
- **Do (owner preview, 0 Gemini):** `/dashboard/settings/calendar` → **Preview availability** → Service `Haircut`, Date = that day → **Check availability**.
- **Expect:** **The receptionist would offer** lists up to 3 times inside opening hours; none overlaps 14:00-15:00 (a 45-minute Haircut cannot start between 13:20 and 14:55).
- **Do (chat):** in the same test chat, send: `Do you have a haircut on <that date> in the afternoon?`
- **Expect:** the agent offers only times that the preview could also offer; none inside the busy block.
- **Verify in the backend:** log line `"msg": "calendar availability lookup"` on `tether.availability` with its latency (a `... slow` warning above 2 s); `interaction_operations` row `check_availability` `succeeded`; `/dashboard/settings/usage` shows the "Calendar checks" meter rising.
- **Evidence:** `evidence/dod-06-preview.png`, `evidence/dod-06-calendar-busy.png` (Google Calendar day view), `evidence/dod-06-chat-offer.png`.
- **If it fails:** `reconnect_needed` → **Reconnect** (7-day Testing-mode expiry). `not_connected` or no calendar chosen → redo setup step 4. "Closed" or "Hours not set" → pick a Tue-Sat date and confirm the seed wrote hours.

### Step 3 (DoD 7): create the appointment and verify it in Google Calendar

- **Preconditions:** step 2's offer is the last thing the agent said.
- **Do:** send: `Please book the first time you offered. My name is Alex Rivera.`
- **Expect:** a confirmation stating exactly the booked time; the bookings panel beside the chat shows the booking as confirmed (no other status reads as booked).
- **Verify in the backend:**
  - Google Calendar: an event `Haircut - Alex Rivera` at that time on `Tether demo (synthetic)`.
  - psql: `select status, start_time, calendar_event_id, customer_name from bookings where business_id = :'biz' order by created_at desc limit 1;` → `confirmed` with a non-null `calendar_event_id`.
  - Log: `"msg": "booking"` on `tether.booking` with `"outcome": "confirmed"`.
  - Re-run **Check availability** for that date: the booked time is gone.
- **Evidence:** `evidence/dod-07-booking.png` (chat + bookings panel), `evidence/dod-07-gcal-event.png`.
- **If it fails:** "that time was just taken" with new options is `slot_taken` (correct behavior; book one of the new times). A booking fallback or "Not booked" must come with no event in Google Calendar; capture it, check `tether.booking` / `tether.calendar` codes, and do not count step 3 as passed.

### Step 4 (DoD 9, 10, 13): transcript and two-sentence summary

- **Do:** click **End chat**. Open `/dashboard/inbox`, then the newest row.
- **Expect:** the detail shows the full transcript (every message from steps 1-3 in order), the verified-actions timeline (`search_faq`, `check_availability`, `create_booking`), outcome booked, and a summary labelled **AI summary** that is exactly two sentences and states the booked time.
- **Verify in the backend:** `select status, outcome, summary_source, summary from interactions where business_id = :'biz' order by started_at desc limit 1;` → `completed`, `booked`, `ai`. `select count(*) from interaction_messages where interaction_id = '<id>';` equals the transcript length.
- **Evidence:** `evidence/dod-09-transcript.png`, `evidence/dod-10-summary.png`, `evidence/dod-13-inbox.png`.
- **If it fails:** **Automatic summary** (`summary_source = template`) means the model's summary failed validation twice, hit a limit, or timed out; look for `summary: ...` warnings on `tether.summarizer`. A template is safe behavior but does not prove DoD 10: repeat steps 1-4 another day.

### Step 5 (DoD 11, 12): escalation creates an alert record and a trial SMS

- **Preconditions:** escalation rules saved with text alerts on and your verified number (setup step 5); `API_PUBLIC_BASE_URL` reachable (status callbacks).
- **Do:** in the test chat, start a **New chat** and send: `Can I talk to a real person, please?`
- **Expect:** a reply that does not promise a callback; the escalation is recorded; within a minute your phone gets an SMS starting with Twilio's trial prefix (`Sent from your Twilio trial account - `), naming the reason and linking to `{APP_BASE_URL}/dashboard/inbox/<id>`, with no customer details. Click **End chat**.
- **Verify in the backend:**
  - `/dashboard/inbox?type=attention`: the row shows **Needs attention · <reason>**; the detail's escalation panel shows the SMS status moving to **Delivered**.
  - psql: `select reason, urgency, status, sms_status, sms_sid, sms_error from escalations where business_id = :'biz' order by created_at desc limit 1;` → `human_requested`, `open`, `delivered`, an `SM...` sid.
  - Log: `"msg": "escalation recorded"` on `tether.escalation`; `"msg": "http request"` on `tether.http` with `"route": "/api/v1/webhooks/twilio/sms-status"` and `"status": 204` (one per Twilio status callback).
  - Twilio console → Monitor → Messaging logs: the message is `delivered`.
- **Evidence:** `evidence/dod-11-escalation.png` (inbox row + panel), `evidence/dod-12-sms.png` (phone screenshot, number blurred), `evidence/dod-12-twilio-log.png`.
- **If it fails:**
  - `Logged (demo mode)`: Twilio settings missing; the API is in demo mode.
  - `SMS off`: text alerts off or no contact phone.
  - `failed` / `Not delivered` with an error code: a provider restriction (unverified recipient, or a carrier/registration block such as Twilio error 30034 or 30007 for US traffic). Record the code as evidence that the restriction was respected; do not pay for registration or upgrade.
  - Status stuck at `sent`/`queued`: the callback could not reach `API_PUBLIC_BASE_URL` (tunnel down or URL changed) or failed its signature (403 on `tether.http`).
  - No escalation at all: the policy did not fire. Do not reword repeatedly (each try costs requests); `Can I speak to a person?` is the most direct wording.

### Step 6 (DoD 8, 9, 10): voice call plays a Gemini TTS voice

- **Preconditions:** the agent is **active**: `/dashboard/agent#activation` → every checklist item ticked (persona, services, hours, calendar, escalation contact, phone, test chat) → **Activate**. A Gemini voice is selected in the persona. Optional: **Preview voice** plays the saved greeting (1 TTS request); save as `evidence/dod-08-preview.wav` or note it.
- **Do:** from your verified phone, call the trial number. Start a recording on the phone or a second device if you can.
  1. Twilio plays its trial announcement first (it may ask you to press a key).
  2. Listen to the greeting: it is the owner's greeting in the chosen Gemini voice, not Twilio's standard voice.
  3. Say: `What time do you close on Saturday?` Expect "4 PM" (seeded 16:00) in the same voice. "One moment, please." first is normal if Gemini is slow.
  4. Say: `That's all, goodbye.` The call ends. Keep the call under 2 minutes (trial credit).
- **Verify in the backend:**
  - Log: `"msg": "call started"` and `"msg": "voice turn"` lines on `tether.voice` with `"audio": "play"` and `tts_ms`; `"msg": "tts"` on `tether.speech` with `"outcome": "ok"` (first synthesis) or `"cached"` (greeting reuse); then `"msg": "call ended"`. This is also the first real check of the TTS response format (24 kHz PCM); a `provider_error` or `error:...` outcome there means `<Say>` was used instead.
  - `/dashboard/inbox`: a voice interaction with the caller masked to its last four digits in the list, the full transcript in the detail (DoD 9) and a two-sentence **AI summary** (DoD 10).
  - psql: `select channel, status, outcome, summary_source from interactions where business_id = :'biz' order by started_at desc limit 1;` → `voice`, `completed`.
  - `/dashboard/settings/usage`: `tts_request` and `call` went up.
- **Evidence:** `evidence/dod-08-voice-call.m4a` (or a video), `evidence/dod-08-voice-log.txt` (the `voice turn` / `tts` lines copied from `api-log.jsonl`), `evidence/dod-09-voice-transcript.png`, `evidence/dod-10-voice-summary.png`.
- **If it fails:**
  - A short polite line and a hang-up: the agent is inactive, the plan is not entitled, or the number is not associated with this business.
  - Twilio's "application error": the webhook URL is unreachable. Quick tunnel URLs change on restart: update `API_PUBLIC_BASE_URL`, restart the API, `/dashboard/phone` → **Test** (`misconfigured`, `voice_url_mismatch`) → **Disconnect** → **Connect** again.
  - A robotic Twilio voice with `"audio": "say"`: TTS fell back (limit, timeout, `tts_not_configured`). The call still works, but DoD 8 is not proven: check the `tts` outcome and retry another day if it was `limit_reached` or `rate_limited`.

---

## 4. Failure-safety spot checks (optional, real providers)

### F1 (DoD 15): calendar access lost mid-demo → no false confirmation

1. At https://myaccount.google.com/permissions, remove the Tether app's access (this revokes the grant at Google).
2. New test chat: `Can I book a haircut on <a Tue-Fri date> at 10:00? My name is Sam Lee.`
3. **Expect:** no booking claim; the agent says it cannot check or book right now and gives the business's contact details. The bookings panel shows nothing confirmed.
4. **Verify:** `/dashboard/settings/calendar` shows **Reconnect needed**; `/dashboard/settings/integrations` lists the error; psql `select count(*) from bookings where business_id = :'biz' and customer_name = 'Sam Lee' and status = 'confirmed';` → 0; no new event in Google Calendar.
5. **Restore:** **Reconnect** and select the calendar again.
6. **Evidence:** `evidence/dod-15-calendar-revoked.png`.

### F2 (DoD 14): Gemini quota reached → quota fallback

1. Read today's `gemini_request` count N on `/dashboard/settings/usage`.
2. Set `GEMINI_REQUESTS_PER_BUSINESS_PER_DAY=N` in the API settings and restart the API.
3. Send any test chat message.
4. **Expect:** the **AI limit reached** callout and a reply with the business's own phone/email (no promised callback). The count stays N: the guard refused before calling Gemini, so this costs nothing.
5. **Verify:** the Usage panel shows the Gemini meter at its limit; psql `select state->'requires_human' from interactions where business_id = :'biz' order by started_at desc limit 1;` → `true`.
6. **Restore:** unset the variable (default 50) and restart.
7. **Evidence:** `evidence/dod-14-quota-fallback.png`.

---

## 5. Evidence checklist

| DoD | Item | Evidence | Done | Date | Initials |
|---|---|---|---|---|---|
| 1 | Create and configure a test business | CI green (API, Web); E2E report (PR 3/3) | [ ] | | |
| 2 | Persona | CI green; E2E report | [ ] | | |
| 3 | FAQs | CI green; E2E report | [ ] | | |
| 4 | Services | CI green; E2E report | [ ] | | |
| 5 | Gemini interprets a real request | `dod-05-gemini-chat.png`, `usage-before.png` | [ ] | | |
| 6 | Real Google Calendar slots | `dod-06-preview.png`, `dod-06-calendar-busy.png`, `dod-06-chat-offer.png` | [ ] | | |
| 7 | Appointment created and verified | `dod-07-booking.png`, `dod-07-gcal-event.png` | [ ] | | |
| 8 | Voice plays a Gemini TTS response | `dod-08-voice-call.m4a`, `dod-08-voice-log.txt` | [ ] | | |
| 9 | Transcript recorded | `dod-09-transcript.png`, `dod-09-voice-transcript.png` | [ ] | | |
| 10 | Two-sentence AI summary | `dod-10-summary.png`, `dod-10-voice-summary.png` | [ ] | | |
| 11 | Escalation alert record | `dod-11-escalation.png` | [ ] | | |
| 12 | Trial SMS within restrictions | `dod-12-sms.png`, `dod-12-twilio-log.png` | [ ] | | |
| 13 | Owner reviews in the dashboard | `dod-13-inbox.png`; CI + E2E | [ ] | | |
| 14 | Usage limits | CI (`test_usage.py`, `test_rate_limit.py`); optional `dod-14-quota-fallback.png` | [ ] | | |
| 15 | AI and calendar failures handled safely | CI (`tests/chaos/`); optional `dod-15-calendar-revoked.png` | [ ] | | |
| 16 | Tenant isolation | CI (`tests/security/test_tenant_isolation_sweep.py`) | [ ] | | |

Also attach `usage-after.png` (the Usage panel after step 6) so the request budget is on record.

---

## 6. Cleanup

1. `/dashboard/settings/data` → **Delete test data** → type `DELETE TEST DATA` → confirm. Expect the counts (conversations, messages, bookings, escalations, usage events, calendar events cancelled). If **Some calendar events remain**, delete them in Google Calendar by hand. The agent, services, FAQs, hours, calendar connection and phone number stay; today's usage counters stay on purpose.
2. `/dashboard/agent#activation` → **Deactivate** (callers then hear that you're unavailable).
3. `/dashboard/phone` → **Disconnect** the trial number. This clears the Voice URL and status callback at Twilio. If the dialog warns that Twilio did not confirm, clear them in the Twilio console.
4. `/dashboard/settings/calendar` → **Disconnect** (revokes the grant). If Google did not confirm, remove access at https://myaccount.google.com/permissions. Delete the `Tether demo (synthetic)` calendar and the `Runbook block` event.
5. Stop cloudflared (the quick-tunnel URL dies with it) and `stripe listen`.
6. Restore any limit you changed (`GEMINI_REQUESTS_PER_DAY`, `GEMINI_REQUESTS_PER_BUSINESS_PER_DAY`) and restart the API.
7. Move the evidence to the PR or issue #17 with phone numbers and emails blurred, then delete the local copy of `api-log.jsonl` (it holds ids, not message bodies, but needs no keeping).
