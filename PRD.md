# Tether

## Product Requirements Document (PRD)

**Product:** Tether
**Version:** 2.0
**Product Category:** B2B SaaS, AI Receptionist
**Target Customers:** Local service businesses
**Initial Verticals:** Salons, barbershops, spas, trades, repair services, and general appointment-based businesses
**Platform:** Web application with AI-powered phone reception
**Primary Goal:** Build a functional AI receptionist prototype using free-tier services.

---

# 1. Executive Summary

Tether is an AI-powered receptionist that helps local businesses handle customer calls, answer frequently asked questions, book appointments, and escalate complicated requests to a human.

Small businesses frequently miss incoming calls because employees are helping customers, working on-site, or unavailable outside business hours. These missed calls can lead to lost bookings and revenue.

Tether solves this problem by giving each business a configurable AI receptionist accessible through a dedicated phone number.

Business owners manage their receptionist through a web dashboard. They can configure its personality, upload FAQs, define services and opening hours, connect Google Calendar, and review customer interactions.

When a customer calls, Tether processes the conversation with Google Gemini, checks relevant business information, checks calendar availability when needed, and performs validated actions through the Python backend.

## 1.1 Product Value Proposition

**Never miss a customer inquiry just because you're busy.**

Tether helps businesses:

* Answer common questions automatically.
* Capture potential customer leads.
* Book appointments without manual intervention.
* Maintain a record of customer conversations.
* Receive alerts when a human needs to step in.

## 1.2 Core Product Flow

Business owner configures Tether
→ Connects Google Calendar
→ Activates a phone number
→ Customer calls
→ Gemini interprets the request
→ FastAPI executes the appropriate business logic
→ Tether answers or books an appointment
→ Interaction is saved in PostgreSQL
→ Owner reviews the interaction or receives an escalation alert.

---

# 2. Product Goals

## 2.1 Primary Goals

1. Build a working AI receptionist using a real LLM API.
2. Allow non-technical users to configure the receptionist without coding.
3. Answer FAQs using business-provided knowledge.
4. Identify booking requests and extract appointment details.
5. Check live Google Calendar availability.
6. Create calendar appointments after validating the requested slot.
7. Generate interaction transcripts and two-sentence summaries.
8. Escalate uncertain, complex, or urgent interactions.
9. Keep AI usage within available Gemini free-tier quotas during prototyping.
10. Use an architecture that can be migrated to production infrastructure later.

## 2.2 Secondary Goals

* Reduce missed leads.
* Reduce repetitive administrative work.
* Improve customer response times.
* Provide transparent records of AI decisions and actions.
* Keep operating costs predictable through strict usage limits.

## 2.3 Non-Goals for the MVP

The MVP will not include:

* A native mobile application.
* A complete CRM.
* Payment processing.
* Outbound sales calls.
* Voice cloning.
* Fully real-time, interruptible voice streaming.
* Advanced analytics and business intelligence.
* Medical diagnosis or emergency handling.
* Multiple calendar providers.
* Complex employee scheduling.
* Multiple AI providers.

---

# 3. Updated Technology Stack

## 3.1 Frontend

**Next.js + TypeScript**

Responsibilities:

* Landing page.
* Authentication screens.
* Business dashboard.
* Agent Builder.
* FAQ and service management.
* Calendar connection interface.
* Interaction inbox.
* Transcript and summary viewer.
* Usage monitoring.
* Settings and escalation configuration.

Recommended libraries:

* Tailwind CSS for styling.
* shadcn/ui for interface components.
* React Hook Form for forms.
* Zod for client-side form validation.
* A lightweight charting library only if analytics requires it.

The frontend must not call Gemini or Twilio directly. All secret-bearing operations pass through the backend.

## 3.2 Backend

**Python + FastAPI**

FastAPI is the selected backend framework.

Reasons:

* Built specifically for APIs.
* Supports Python type hints and Pydantic validation.
* Provides automatic OpenAPI documentation.
* Works well with external API integrations.
* Makes it straightforward to separate AI reasoning from deterministic business logic.
* Has an officially documented Vercel deployment path.

Django is not the preferred option for this MVP. Its built-in administration, authentication, and broader framework features are useful for larger traditional applications, but Tether primarily needs a small API and webhook backend.

### FastAPI responsibilities

* Gemini API integration.
* Conversation orchestration.
* Prompt construction.
* Structured AI output validation.
* Booking logic.
* Google Calendar API integration.
* Twilio voice and SMS webhooks.
* Interaction and transcript storage.
* Summary generation.
* Escalation decisions and notifications.
* Authentication and tenant authorization.
* API usage limits.
* Error handling and logging.

## 3.3 Database

**Neon + PostgreSQL**

PostgreSQL stores:

* Users.
* Businesses.
* Agent configuration.
* FAQs.
* Services.
* Business hours.
* Calendar connections.
* Phone numbers.
* Interactions.
* Conversation messages.
* Bookings.
* Escalations.
* Usage events.

Use Neon’s pooled PostgreSQL connection string for serverless connections.

## 3.4 Python ORM and Migrations

Use:

* SQLAlchemy 2.x for database operations.
* Alembic for schema migrations.
* Pydantic for API request and response schemas.

**Important:** Drizzle ORM is designed for the TypeScript ecosystem. Since the backend is now Python, use SQLAlchemy in FastAPI rather than trying to use Drizzle from the Python backend.

## 3.5 AI

**Google Gemini API**

Gemini is the only AI provider for the MVP.

AI responsibilities:

* Understand customer intent.
* Extract structured information from customer speech or text.
* Determine what information is missing.
* Generate conversational responses.
* Answer questions using business-provided information.
* Generate two-sentence interaction summaries.
* Classify escalation requirements.
* Generate speech using an available Gemini text-to-speech model.

Model names and rate limits must be configurable because Google's available models and free-tier quotas may change.

## 3.6 External Integrations

| Service             | Purpose                                                    |
| ------------------- | ---------------------------------------------------------- |
| Twilio              | Phone numbers, inbound calls, and SMS                      |
| Google Calendar API | Availability and appointment management                    |
| Gemini API          | Conversation intelligence and text-to-speech               |
| Neon                | PostgreSQL hosting                                         |
| Vercel              | Frontend and serverless backend hosting during prototyping |

---

# 4. Hosting Architecture Decision

## 4.1 Recommended Setup

For the simplest initial deployment:

* Deploy the Next.js frontend as a Vercel project.
* Deploy FastAPI as a separate Vercel project.
* Host PostgreSQL on Neon.
* Connect the frontend to the FastAPI backend through an API URL.
* Configure CORS to allow only the frontend's origin.
* Keep backend credentials in server-side environment variables.

Using two Vercel projects gives the frontend and Python runtime separate deployment configurations.

Example configuration:

Frontend:

`NEXT_PUBLIC_API_BASE_URL=https://your-fastapi-project.vercel.app`

Backend:

`DATABASE_URL=your_neon_pooled_connection_string`

`GEMINI_API_KEY=your_gemini_api_key`

`TWILIO_ACCOUNT_SID=your_twilio_account_sid`

`TWILIO_AUTH_TOKEN=your_twilio_auth_token`

`GOOGLE_CLIENT_ID=your_google_client_id`

`GOOGLE_CLIENT_SECRET=your_google_client_secret`

These are environment-variable names, not actual credentials.

## 4.2 Vercel Limitations

The backend must be designed for serverless execution.

Requirements:

* Each API request must be independently executable.
* No reliance on in-memory conversation state between requests.
* No persistent background worker.
* No requirement for a permanently running server.
* No native WebSocket server for real-time phone audio.
* Database connections must be reused carefully or managed through Neon pooling.
* Long-running operations must respect Vercel function timeouts.

For the MVP, use a turn-based telephone conversation. A later version can move real-time voice streaming to a persistent backend host.

## 4.3 $0 Development Constraint

The prototype must use free-tier services wherever available.

However, the development budget and production operating budget are different.

The free-tier prototype must:

* Use synthetic customer details.
* Avoid sending real confidential customer records to Gemini.
* Use limited test interactions.
* Use Twilio trial capabilities where available.
* Stop processing when configured quotas are exhausted.
* Avoid purchasing a production phone number until a budget is approved.

A live commercial Tether service will require a review of hosting eligibility, privacy terms, and communication costs before launch.

---

# 5. User Personas

## Persona A: Local Business Owner

**Example:** A salon owner with a small team.

### Needs

* Answer every incoming customer inquiry.
* Book appointments while employees serve other customers.
* Reduce repetitive questions.
* Know when a customer needs human assistance.

### Pain Points

* Missed calls.
* Frequent interruptions.
* Manual scheduling.
* Leads that never call back.

### Expected Outcome

The owner configures Tether once and can review calls, bookings, and escalations from a central dashboard.

## Persona B: Front Desk Manager

**Example:** A clinic or beauty studio administrator.

### Needs

* Keep FAQs accurate.
* Monitor appointments.
* Review conversations.
* Resolve escalated requests quickly.

### Expected Outcome

Routine calls are handled automatically, while unresolved requests appear in an organized inbox.

## Persona C: Customer / Caller

**Example:** Someone looking for an available haircut appointment.

### Needs

* Get a quick answer.
* Understand services and prices.
* Book a convenient time.
* Receive a clear confirmation.

### Expected Outcome

The customer can complete a straightforward task without waiting for a human receptionist.

---

# 6. Core User Journeys

## 6.1 Business Onboarding

1. User creates an account.
2. User creates a business workspace.
3. User enters business details and timezone.
4. User configures the AI receptionist persona.
5. User adds services, prices, and durations.
6. User uploads or creates FAQs.
7. User configures opening hours and booking rules.
8. User connects Google Calendar.
9. User configures an escalation contact.
10. User connects a test Twilio number.
11. User tests the agent.
12. User activates the agent.

## 6.2 FAQ Interaction

1. Customer calls the business number.
2. The call is routed to Tether.
3. Tether captures the customer's utterance.
4. Gemini interprets the request.
5. Backend retrieves relevant business information.
6. Gemini creates an answer grounded in that information.
7. Tether delivers the response.
8. The interaction is saved to PostgreSQL.
9. Gemini generates a two-sentence summary when the interaction ends.

## 6.3 Appointment Booking

1. Customer requests an appointment.
2. Gemini identifies the requested service and any date or time preference.
3. Tether asks for missing details.
4. Backend validates the service.
5. Backend checks Google Calendar.
6. Tether presents available times.
7. Customer chooses a time.
8. Backend checks availability again.
9. Backend creates the calendar event.
10. Tether confirms the booking only after successful creation.
11. The booking is associated with the interaction.

## 6.4 Escalation

1. Gemini identifies a request that requires human intervention.
2. Backend validates the escalation classification.
3. An escalation record is created.
4. Tether sends an SMS if messaging is enabled.
5. The inbox marks the interaction as requiring attention.
6. The business owner reviews the transcript and follows up.

---

# 7. User Stories and Acceptance Criteria

## 7.1 Agent Builder

### US-001: Configure Agent Identity

As a business owner, I want to configure the receptionist's name and introduction so it represents my business.

Acceptance criteria:

* The owner can define an agent name.
* The owner can edit the introduction.
* Settings are persisted to PostgreSQL.
* Updated settings are loaded by subsequent conversations.
* The agent uses the configured identity in its responses.

### US-002: Configure Agent Personality

As a business owner, I want to select a communication style.

Acceptance criteria:

* Supported presets include friendly, professional, and concise.
* The owner can add custom instructions.
* Instructions are inserted into the Gemini system prompt.
* Business owners cannot override platform safety rules.

### US-003: Configure Business Knowledge

As a business owner, I want to add FAQs so the agent can answer common questions.

Acceptance criteria:

* The owner can create, edit, delete, and disable FAQs.
* Each FAQ contains a question and answer.
* FAQs are associated with a business.
* Disabled FAQs are excluded from the agent's context.
* The agent must not invent missing answers.

## 7.2 Services and Calendar

### US-004: Manage Services

As a business owner, I want to define bookable services with prices and durations.

Acceptance criteria:

* Each service has a name and duration.
* Price and description are optional when appropriate.
* Inactive services cannot be booked.
* Booking duration comes from stored service data.

### US-005: Check Availability

As a customer, I want Tether to provide available appointment times.

Acceptance criteria:

* Availability is retrieved through the Google Calendar API.
* Business hours and service duration are considered.
* Times are shown in the business's configured timezone.
* Tether never invents availability.

### US-006: Book an Appointment

As a customer, I want Tether to make my booking.

Acceptance criteria:

* Required details are collected.
* Availability is checked again immediately before creation.
* The event is created through Google Calendar.
* The booking is not reported as successful if the API fails.
* The resulting event ID is stored.

## 7.3 Interaction Inbox

### US-007: View Interactions

As a business owner, I want to review all customer interactions.

Acceptance criteria:

* The inbox supports pagination.
* The owner can filter by intent, status, and date.
* Each interaction displays its summary and outcome.
* Only authorized users can view their business's records.

### US-008: View Transcripts

As a business owner, I want to inspect what happened during a call.

Acceptance criteria:

* Messages are stored in chronological order.
* Speaker labels distinguish customer and receptionist.
* Failed and incomplete interactions are identifiable.
* Sensitive information is minimized.

## 7.4 Escalation

### US-009: Receive Alerts

As a business owner, I want to receive an SMS when a customer needs human assistance.

Acceptance criteria:

* Configured escalation rules trigger an event.
* Duplicate alerts are prevented through idempotency controls.
* The SMS includes a concise reason and interaction reference.
* SMS delivery status is stored.
* Failed delivery is logged.

## 7.5 Phone Setup

### US-010: Connect a Phone Number

As a business owner, I want to associate a phone number with my business.

Acceptance criteria:

* The number is associated with exactly the intended business.
* Webhook configuration is verified.
* The interface displays connection status.
* Test calls can be performed before activation.
* Trial-account restrictions are clearly explained.

---

# 8. Functional Requirements

## 8.1 Authentication and Authorization

The application must support:

* Registration.
* Login and logout.
* Session expiration.
* Password recovery or provider-managed account recovery.
* Business workspace membership.
* Role-based access if staff accounts are introduced.

Every backend request must authenticate the user or validate the external service webhook.

The backend must derive business access from authenticated identity and database relationships. It must never trust a user-supplied `business_id` without checking authorization.

## 8.2 Business Workspace

Each workspace stores:

* Business name.
* Industry.
* Business address.
* Timezone.
* Business hours.
* Public contact details.
* Owner escalation settings.
* Creation and modification timestamps.

All business-specific data must be isolated by workspace.

## 8.3 No-Code Agent Builder

The owner can configure:

* Agent name.
* Tone and persona.
* Greeting.
* Business-specific instructions.
* FAQ content.
* Escalation rules.
* Booking behavior.

The system should assemble these settings into a controlled prompt template. Users can configure the receptionist's style, but they must not be able to remove platform-level safety instructions.

## 8.4 FAQ Management

Each FAQ contains:

* Question.
* Answer.
* Category.
* Active status.
* Creation timestamp.
* Modification timestamp.

Initially, use explicit question-and-answer entries instead of a complex document ingestion or vector search pipeline.

For small local businesses, sending a limited set of relevant FAQs in the prompt is simpler and easier to debug.

The application should avoid injecting unrelated FAQs into every model request.

## 8.5 Service Management

Each service contains:

* Name.
* Description.
* Duration in minutes.
* Price, when applicable.
* Active status.
* Optional calendar mapping.

The backend must validate service names and identifiers before booking.

The agent must not invent service durations or prices.

## 8.6 Gemini Conversation Engine

Gemini is responsible for interpreting the customer's intent and producing a structured response.

Supported initial intents:

* `FAQ`
* `BOOK_APPOINTMENT`
* `BUSINESS_HOURS`
* `LOCATION`
* `PRICE_INQUIRY`
* `RESCHEDULE`
* `CANCEL`
* `HUMAN_REQUEST`
* `COMPLAINT`
* `URGENT`
* `UNKNOWN`

The backend must validate Gemini's response against a Pydantic schema before taking action.

A model response is never itself proof that a booking, SMS, or other external operation succeeded.

### Example structured response

```
{
  "intent": "BOOK_APPOINTMENT",
  "response_text": "What day would you like to come in?",
  "entities": {
    "service_name": "Haircut",
    "requested_date": null,
    "requested_time": null,
    "customer_name": null
  },
  "requires_human": false
}
```

The values are illustrative. Actual responses must follow the current schema and supported data types.

## 8.7 Booking Engine

The booking engine must:

1. Identify the requested service.
2. Determine missing booking details.
3. Ask for missing information.
4. Resolve relative dates using the business timezone.
5. Query Google Calendar for conflicts.
6. Generate valid appointment options.
7. Offer available options to the customer.
8. Recheck availability after the customer selects a slot.
9. Create the calendar event.
10. Save the booking record.
11. Return the actual operation result to Gemini.
12. Confirm the appointment only after success.

If Google Calendar is unavailable, Tether must not claim that an appointment has been booked.

## 8.8 Google Calendar Integration

The MVP supports Google Calendar only.

Required capabilities:

* OAuth connection.
* Secure token storage.
* Calendar selection.
* Free/busy lookup.
* Appointment creation.
* Appointment cancellation when implemented.
* Basic synchronization status.
* OAuth disconnection.

Access tokens should be refreshed using the official OAuth flow. Tokens must never be exposed to the frontend.

A disconnected or invalid calendar connection must be clearly displayed to the business owner.

## 8.9 Conversation Inbox

The inbox must display:

* Date and time.
* Caller identifier where available.
* Intent.
* Interaction status.
* Two-sentence summary.
* Booking outcome.
* Escalation status.

Supported filters:

* All interactions.
* Bookings.
* FAQs.
* Escalations.
* Failed interactions.
* Date range.

Selecting an interaction opens its detailed transcript and operation history.

## 8.10 AI Summaries

At the end of an interaction, Gemini generates exactly two concise sentences.

The summary should state:

1. What the customer wanted.
2. What happened.

Example:

"Customer called to book a haircut for Friday afternoon. The appointment was successfully added to Google Calendar for 3:30 PM."

Summaries must be based on the stored transcript and verified backend results. The model must not describe a failed booking as successful.

## 8.11 Automated SMS Escalation

The backend triggers escalation when:

* The customer requests a human.
* The agent cannot answer after the configured clarification attempts.
* A booking cannot be resolved.
* A configured urgent or sensitive situation is detected.
* An external integration repeatedly fails during an important interaction.

Gemini may classify the reason, but deterministic rules must also be applied in code.

The backend creates an escalation event before attempting to send the SMS. It then records the provider response and delivery status.

Example alert:

"Tether alert: A customer needs help with an appointment issue. Open your Tether inbox to review the interaction."

The message should omit unnecessary personal details.

## 8.12 Twilio Voice Integration

Twilio handles the incoming telephone call and sends requests to the backend.

Required capabilities:

* Associate an incoming number with a business.
* Receive call webhooks.
* Capture caller utterances.
* Deliver generated voice responses.
* Track call status.
* Terminate completed calls cleanly.
* Record interaction metadata.

The initial voice experience must use a turn-based request/response design compatible with serverless hosting.

Recommended concept:

1. Twilio sends a voice webhook to FastAPI.
2. Tether plays a greeting.
3. The caller speaks.
4. Twilio provides the captured audio or recording reference through its webhook flow.
5. FastAPI retrieves the audio where necessary.
6. Gemini interprets the utterance and identifies the intent.
7. The application executes required tools.
8. Gemini generates the response text.
9. Gemini TTS generates speech audio.
10. Tether returns instructions for Twilio to play the response.
11. The call continues for another turn or ends.

The implementation must account for webhook timeouts, temporary audio storage, media retrieval, and usage charges associated with telephony and recording.

Persistent real-time audio streaming is excluded from the first version because it requires a different architecture from ordinary Vercel serverless functions.

## 8.13 Twilio Number Management

The dashboard must support associating a phone number with a business.

Where trial restrictions permit, the owner should be able to:

* View available numbers.
* Select a number.
* Associate it with the business.
* Configure webhook routing.
* Test an inbound call.
* Disconnect a number.

Provisioning a production number must require an explicit action and clear confirmation that it may incur charges.

Do not automatically purchase numbers during account registration.

## 8.14 Usage Controls

Track usage per business and globally.

Required counters:

* Gemini requests.
* Gemini input and output tokens where available.
* Gemini TTS requests.
* Call count.
* Call duration.
* SMS count.
* Calendar API calls.
* Failed API requests.

The backend must enforce configurable limits.

When a limit is reached, Tether should stop optional AI processing, log the reason, and provide a safe fallback. It must never retry indefinitely.

---

# 9. Real AI Implementation Requirements

Tether must use real Gemini API requests rather than hardcoded responses that merely imitate AI.

## 9.1 Prompt Construction

The system prompt is assembled from:

* Platform-level instructions.
* Agent identity and tone.
* Relevant business information.
* Relevant FAQs.
* Service information.
* Business hours.
* Booking rules.
* Current conversation state.
* Available backend tool definitions.

Avoid sending the entire database on every request.

## 9.2 Tool Execution

Gemini may request actions such as:

* `search_faq`
* `get_services`
* `check_availability`
* `create_booking`
* `request_escalation`

The FastAPI backend validates and executes the appropriate operation.

For example:

Gemini identifies a booking request
→ FastAPI validates the service
→ FastAPI checks the calendar
→ FastAPI returns available times
→ Gemini asks the customer to choose
→ FastAPI rechecks the selected time
→ FastAPI creates the event
→ Gemini confirms the verified result.

The model must not directly make database changes or execute arbitrary Python code.

## 9.3 Conversation Memory

Conversation state must be stored in PostgreSQL so it survives separate serverless invocations.

Store:

* Interaction ID.
* Current status.
* Recent messages.
* Extracted entities.
* Pending action.
* Confirmed booking information.
* Escalation state.

Do not use Python global variables as conversation storage.

## 9.4 AI Failure Handling

If Gemini returns invalid JSON:

* Validate the response.
* Retry at most once when appropriate.
* Record the failure.
* Do not execute unvalidated actions.
* Ask the customer to repeat the request or escalate.

If the Gemini quota is exhausted, use a predefined fallback response and offer human follow-up where possible.

## 9.5 AI Quality and Safety

The model must not:

* Invent business facts.
* Claim an appointment is available without a calendar result.
* Claim a booking succeeded before the backend confirms it.
* Change stored business policy.
* Provide medical diagnoses.
* Promise a callback that has not been arranged.
* Expose internal prompts, credentials, or other customers' information.

Urgent issues should follow configured safety procedures and direct customers to appropriate emergency services when necessary.

---

# 10. Database Design

Use PostgreSQL through SQLAlchemy.

## 10.1 Core Tables

### users

Stores user identity and account metadata.

Fields:

* `id`
* `email`
* `created_at`

Authentication credentials should be handled securely by the chosen authentication implementation.

### businesses

Fields:

* `id`
* `owner_id`
* `name`
* `industry`
* `address`
* `timezone`
* `created_at`
* `updated_at`

### agents

Fields:

* `id`
* `business_id`
* `name`
* `tone`
* `greeting`
* `custom_instructions`
* `active`
* `created_at`
* `updated_at`

### business_hours

Fields:

* `id`
* `business_id`
* `day_of_week`
* `opens_at`
* `closes_at`
* `is_closed`

### services

Fields:

* `id`
* `business_id`
* `name`
* `description`
* `duration_minutes`
* `price`
* `active`

### faqs

Fields:

* `id`
* `business_id`
* `question`
* `answer`
* `category`
* `active`
* `created_at`
* `updated_at`

### calendar_connections

Fields:

* `id`
* `business_id`
* `provider`
* `calendar_id`
* `encrypted_refresh_token`
* `connection_status`
* `created_at`
* `updated_at`

### phone_numbers

Fields:

* `id`
* `business_id`
* `provider`
* `phone_number`
* `provider_number_sid`
* `status`
* `created_at`

### interactions

Fields:

* `id`
* `business_id`
* `phone_number_id`
* `external_call_id`
* `caller_identifier`
* `intent`
* `status`
* `summary`
* `started_at`
* `ended_at`
* `created_at`

### interaction_messages

Fields:

* `id`
* `interaction_id`
* `speaker`
* `message_text`
* `sequence_number`
* `created_at`

### bookings

Fields:

* `id`
* `business_id`
* `interaction_id`
* `service_id`
* `calendar_event_id`
* `customer_name`
* `customer_phone`
* `start_time`
* `end_time`
* `status`
* `created_at`

### escalations

Fields:

* `id`
* `business_id`
* `interaction_id`
* `reason`
* `urgency`
* `status`
* `sms_status`
* `created_at`

### usage_events

Fields:

* `id`
* `business_id`
* `interaction_id`
* `provider`
* `event_type`
* `units`
* `estimated_cost`
* `created_at`

Estimated cost should be tracked where useful, including for services that are temporarily operating under a trial or free quota.

## 10.2 Database Rules

* Use UUID or another consistent identifier strategy.
* Add indexes for `business_id`, timestamps, interaction status, and external call IDs.
* Add unique constraints where appropriate.
* Use foreign keys.
* Keep schema migrations in Alembic.
* Run migrations separately from normal application startup.
* Use Neon connection pooling.
* Never rely on local serverless filesystem storage for permanent data.

---

# 11. API Design

All endpoints should use consistent JSON responses and HTTP status codes.

The exact prefix may be `/api/v1`.

## Authentication and Business

* `GET /api/v1/me`
* `GET /api/v1/business`
* `POST /api/v1/business`
* `PATCH /api/v1/business/{business_id}`

## Agent

* `GET /api/v1/agent`
* `PATCH /api/v1/agent`
* `POST /api/v1/agent/test`

## FAQs

* `GET /api/v1/faqs`
* `POST /api/v1/faqs`
* `PATCH /api/v1/faqs/{faq_id}`
* `DELETE /api/v1/faqs/{faq_id}`

## Services

* `GET /api/v1/services`
* `POST /api/v1/services`
* `PATCH /api/v1/services/{service_id}`
* `DELETE /api/v1/services/{service_id}`

## Calendar

* `GET /api/v1/calendar/connect`
* `GET /api/v1/calendar/callback`
* `GET /api/v1/calendar/status`
* `GET /api/v1/calendar/availability`

## Interactions

* `GET /api/v1/interactions`
* `GET /api/v1/interactions/{interaction_id}`
* `GET /api/v1/interactions/{interaction_id}/messages`

## Phone Numbers

* `GET /api/v1/phone-numbers`
* `POST /api/v1/phone-numbers`
* `POST /api/v1/phone-numbers/{phone_number_id}/test`

## Twilio Webhooks

* `POST /webhooks/twilio/voice`
* `POST /webhooks/twilio/input`
* `POST /webhooks/twilio/status`
* `POST /webhooks/twilio/sms-status`

## Media

* `GET /media/{media_token}`

The media endpoint must use a short-lived, difficult-to-guess token and return only the intended temporary audio resource.

Webhook routes must validate Twilio signatures. External webhooks must be idempotent wherever the provider may retry requests.

---

# 12. Frontend Requirements

## 12.1 Dashboard

The dashboard should display:

* Total interactions.
* Successful bookings.
* FAQs answered.
* Escalations requiring attention.
* Recent interactions.
* Current integration status.

Metrics must be based on database records rather than placeholder numbers.

## 12.2 Agent Builder

Sections:

1. Identity.
2. Personality.
3. Business information.
4. FAQs.
5. Services.
6. Business hours.
7. Escalation rules.
8. Test interaction.
9. Activation.

The interface should be usable by a business owner without technical knowledge.

## 12.3 Interaction Inbox

Each row should show:

* Time.
* Caller identifier.
* Intent.
* Summary.
* Outcome.
* Escalation status.

Interaction details show the full transcript and verified backend actions.

## 12.4 Integration Settings

The owner must be able to see:

* Google Calendar connection status.
* Selected calendar.
* Phone number status.
* Agent activation status.
* Integration error messages.
* Usage limits when available.

---

# 13. Non-Functional Requirements

## 13.1 Performance

Initial targets:

* Typical dashboard API response under 1 second, excluding third-party latency.
* Calendar availability lookup under 2 seconds in normal conditions.
* Aim for a generated voice response to begin within 5 seconds of a completed caller utterance.
* Paginate transcript and inbox queries.
* Avoid unnecessary Gemini calls.

These are targets for testing, not guaranteed service levels.

## 13.2 Reliability

* Gracefully handle provider timeouts.
* Use bounded retries with backoff.
* Record failed operations.
* Prevent duplicate bookings and duplicate escalation actions.
* Do not confirm failed actions.
* Preserve interaction records if AI generation fails.

## 13.3 Security

* HTTPS for all external requests.
* Store secrets in server-side environment variables.
* Never expose secrets in frontend code.
* Encrypt OAuth refresh tokens at rest.
* Verify Twilio signatures.
* Restrict CORS to known frontend origins.
* Validate inputs through Pydantic.
* Enforce authorization on every business-specific query.
* Rate-limit sensitive endpoints.
* Sanitize logs.

## 13.4 Privacy

Customer conversations may contain personal information.

The MVP must use synthetic data for testing until the applicable AI provider terms, hosting terms, consent requirements, and data-retention policies have been reviewed.

Requirements:

* Collect only necessary information.
* Avoid storing payment information.
* Provide a deletion mechanism for test data.
* Minimize audio retention.
* Avoid including sensitive customer details in SMS alerts.
* Do not claim healthcare compliance.
* Do not market the prototype for handling sensitive medical information without further privacy and compliance review.

## 13.5 Maintainability

The backend should use a modular structure:

* `api`
* `core`
* `models`
* `schemas`
* `services`
* `integrations`
* `repositories`
* `tests`

The Gemini client, Twilio client, and Google Calendar client must be separated from route handlers.

---

# 14. Cost and Free-Tier Requirements

The project should minimize expenses during development, but the system must be explicit about limits.

## 14.1 Gemini

* Use a currently free-tier-eligible Gemini model.
* Use an eligible Gemini TTS model when available.
* Keep model IDs configurable.
* Keep prompts concise.
* Enforce daily and per-business request limits.
* Monitor current rate limits.
* Stop or degrade gracefully when the quota is exhausted.
* Do not send real confidential customer information through the free-tier API without confirming that its terms are appropriate.

The Gemini free tier is suitable for experimentation, but it must not be treated as an unconditional privacy guarantee.

## 14.2 Vercel

Use Vercel Hobby only for personal development and non-commercial testing.

Before operating Tether as a commercial SaaS, choose hosting that explicitly permits commercial use and satisfies the expected privacy, reliability, and usage requirements.

## 14.3 Neon

Use the Neon Free plan for early development, within its current usage limits.

Configure:

* Connection pooling.
* Query timeouts.
* Schema migrations.
* Usage monitoring.
* A clear strategy for database backups before storing real customer data.

## 14.4 Twilio

Use trial functionality for development where available.

Requirements:

* Never purchase a number without explicit confirmation.
* Track calls, minutes, recordings, and messages.
* Warn the owner about trial restrictions.
* Provide a no-phone-number demo mode.
* Do not claim live phone operation is permanently free.

## 14.5 Usage Budget Enforcement

The system must use environment-configured limits for:

* AI requests per day.
* TTS requests per day.
* Maximum call duration.
* Calls per day.
* SMS per day.
* Maximum concurrent interactions.

When a limit is reached, the system should stop optional processing or use a safe fallback instead of causing unbounded retries or charges.

---

# 15. Success Metrics and KPIs

The first prototype should demonstrate functionality before attempting to prove business impact.

## 15.1 Core Product Metrics

### AI Intent Accuracy

Percentage of test utterances for which the correct intent is selected.

Initial target: at least 90% on a curated test set.

### FAQ Answer Accuracy

Percentage of audited answers that are supported by the configured business information.

Initial target: at least 95%.

### Booking Success Rate

Percentage of valid booking attempts successfully written to Google Calendar.

Initial target: at least 98%.

### Double Booking Rate

Percentage of bookings that conflict with an existing calendar event because of Tether.

Target: 0 in the test suite.

### Summary Quality

Percentage of reviewed summaries that accurately describe the customer's request and verified outcome.

Initial target: at least 95%.

### Escalation Recall

Percentage of predefined escalation scenarios correctly escalated.

Initial target: at least 95% in the test set.

### End-to-End Test Pass Rate

Percentage of critical user journeys that pass automated or manual acceptance testing.

Target: 100% of required MVP scenarios before demo release.

### Activation Completion

Percentage of test users who can configure an agent, connect the required integrations, and complete a test call.

Target: at least 80% during usability testing.

## 15.2 Future Business Metrics

After suitable production infrastructure and privacy controls are established, measure:

* Calls answered.
* Qualified leads captured.
* Appointments booked.
* Appointment completion rate where measurable.
* Human escalations.
* Customer satisfaction.
* Estimated influenced revenue.
* Cost per interaction.
* Owner time saved.

Business impact should be measured from actual customer data and compared with an agreed baseline. Do not claim guaranteed revenue recovery.

---

# 16. MVP Delivery Phases

## Phase 1: Foundation

Build:

* Next.js and TypeScript frontend.
* FastAPI backend.
* Neon PostgreSQL.
* SQLAlchemy and Alembic.
* Authentication and workspace isolation.
* Basic dashboard shell.
* Health endpoint.

Completion criteria:

* Frontend can communicate with backend.
* Backend can query PostgreSQL.
* Business records are isolated by ownership.
* Configuration is stored outside source code.

## Phase 2: Agent Builder

Build:

* Agent identity.
* Personality settings.
* Business information.
* Services.
* Business hours.
* FAQ management.
* Prompt construction.

Completion criteria:

* Changing an FAQ affects the next AI response.
* No response is hardcoded to simulate Gemini.
* The agent can answer configured FAQ test cases.

## Phase 3: Gemini Integration

Build:

* Gemini client service.
* Structured intent output.
* Pydantic response validation.
* Conversation state persistence.
* AI usage tracking.
* Retry and fallback logic.

Completion criteria:

* The system can interpret sample customer requests.
* Invalid model output is rejected safely.
* The system never executes an unvalidated AI action.

## Phase 4: Google Calendar

Build:

* Google OAuth.
* Calendar selection.
* Free/busy lookup.
* Booking logic.
* Conflict protection.
* Calendar error handling.

Completion criteria:

* A test appointment is created successfully.
* Conflicting appointments are rejected.
* Failed calendar operations do not produce false confirmations.

## Phase 5: Voice and TTS

Build:

* Twilio test-number integration.
* Incoming-call webhook.
* Audio or recording capture.
* Gemini voice understanding.
* Gemini TTS response generation.
* Temporary audio delivery.
* Call state persistence.

Completion criteria:

* A test caller can complete a turn-based conversation.
* Gemini generates real responses.
* Generated speech can be played to the caller.
* Quota exhaustion and provider failures are handled.

## Phase 6: Inbox and Escalation

Build:

* Conversation transcripts.
* Two-sentence summaries.
* Interaction filters.
* Escalation classification.
* SMS alerts.
* Usage history.

Completion criteria:

* Completed interactions appear in the inbox.
* Summaries match verified outcomes.
* Escalations are recorded and deduplicated.
* Test SMS messages follow the configured limits.

## Phase 7: Hardening

Build:

* Logging.
* Security tests.
* Tenant-isolation tests.
* API limits.
* Failure handling.
* Database migration checks.
* End-to-end test scenarios.

Completion criteria:

* Critical tests pass.
* Secrets are not exposed to the frontend.
* Cross-business data access is blocked.
* Provider failures do not create false bookings.
* Usage limits prevent unbounded requests.

---

# 17. MVP Definition of Done

The prototype is complete when all of the following are demonstrated:

1. A user can create and configure a test business.
2. The owner can define the receptionist's persona.
3. The owner can create and edit FAQs.
4. The owner can configure bookable services.
5. Gemini can interpret a real test customer request.
6. The backend can return available Google Calendar slots.
7. The backend can create an appointment and verify its result.
8. The voice flow can play a Gemini-generated response.
9. The system records an interaction transcript.
10. Gemini produces a two-sentence summary.
11. An escalation scenario creates an alert record.
12. An enabled test SMS can be sent within provider restrictions.
13. The owner can review the interaction in the dashboard.
14. Backend usage limits work.
15. The application handles AI and calendar failures safely.
16. Test workspaces cannot access one another's data.

---

# 18. Final Technical Recommendation

The selected architecture is:

**Next.js + TypeScript** for the frontend.

**Python + FastAPI** for API endpoints, AI orchestration, webhooks, and business logic.

**Neon + PostgreSQL** for persistent data.

**SQLAlchemy + Alembic** for database access and migrations.

**Google Gemini** for conversation reasoning, structured extraction, summaries, and eligible TTS generation.

**Twilio** for telephone calls, number management, and SMS.

**Google Calendar API** for appointment availability and booking.

**Vercel** for the prototype's frontend and serverless backend, subject to its current plan and runtime limitations.

The most important engineering principle is:

**Gemini understands the customer. FastAPI validates the intent. PostgreSQL stores the state. Google Calendar confirms availability. Twilio communicates with the caller.**

This separation keeps the AI implementation real while making critical business actions deterministic, auditable, and easier to test.

The first milestone should be a single-business prototype that can answer an FAQ, book a test appointment, save a transcript, summarize the interaction, and trigger a test escalation. Multi-tenant production launch should come only after hosting, AI data-use terms, privacy requirements, and operating costs have been addressed.
