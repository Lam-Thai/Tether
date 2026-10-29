import type {
  AgentReadiness,
  DashboardSummary,
  IntegrationStatus,
  InteractionRow,
  ReadinessItem,
  UsageRead,
} from "@/lib/api/client";

/** Synthetic data for the dashboard, integrations and activation component tests. */

export const ITEM_DEFS: Record<string, { fix: string; ok: string; bad: string }> = {
  persona: {
    fix: "/dashboard/agent#persona",
    ok: "Name and greeting are set.",
    bad: "Give your receptionist a name and a greeting.",
  },
  services: {
    fix: "/dashboard/agent#services",
    ok: "2 active services.",
    bad: "Add at least one active service.",
  },
  hours: {
    fix: "/dashboard/agent#hours",
    ok: "Opening hours are set.",
    bad: "Set your opening hours.",
  },
  calendar: {
    fix: "/dashboard/settings/calendar",
    ok: "Google Calendar is connected.",
    bad: "Connect Google Calendar so bookings land on your calendar.",
  },
  escalation_contact: {
    fix: "/dashboard/agent#escalation",
    ok: "An escalation contact is set.",
    bad: "Add an escalation contact phone number.",
  },
  phone: {
    fix: "/dashboard/phone",
    ok: "A phone number is connected.",
    bad: "Connect a phone number.",
  },
  test_chat: {
    fix: "/dashboard/agent#test-chat",
    ok: "You've tested your receptionist.",
    bad: "Have a test conversation in the test chat.",
  },
};

export function item(key: string, ok: boolean, over: Partial<ReadinessItem> = {}): ReadinessItem {
  const def = ITEM_DEFS[key] ?? { fix: "/dashboard/agent", ok: "Done.", bad: "Not done." };
  return { key, ok, required: true, message: ok ? def.ok : def.bad, fix_url: def.fix, ...over };
}

/** Every checklist item, with the listed keys failing. */
export function readiness(failing: string[] = [], over: Partial<AgentReadiness> = {}): AgentReadiness {
  const items = Object.keys(ITEM_DEFS).map((key) => item(key, !failing.includes(key)));
  return { active: false, ready: failing.length === 0, items, ...over };
}

export const USAGE: UsageRead = {
  window: { timezone: "UTC", starts_at: "2026-10-28T00:00:00Z", resets_at: "2026-10-29T00:00:00Z" },
  meters: [
    { kind: "gemini_request", used: 3, limit: 50, global_limit_reached: false },
    { kind: "sms", used: 10, limit: 10, global_limit_reached: false },
  ],
  gemini_tokens: 100,
  failed_requests: 0,
  estimated_cost_usd: "0.000100",
  concurrency: { active: 0, limit: 3, idle_minutes: 15, global_limit_reached: false },
  max_call_duration_seconds: 600,
};

export function integrationStatus(over: Partial<IntegrationStatus> = {}): IntegrationStatus {
  return {
    calendar: {
      configured: true,
      status: "connected",
      calendar_name: "Salon",
      last_error: null,
      last_synced_at: "2026-10-28T13:30:00Z",
    },
    phone: {
      configured: true,
      status: "connected",
      phone_number: "+15551234567",
      last_error: null,
      verified_at: "2026-10-28T12:00:00Z",
    },
    agent: { active: true, ready: true },
    readiness: readiness([], { active: true }),
    errors: [],
    usage: USAGE,
    ...over,
  };
}

export const SUMMARY: DashboardSummary = {
  from: "2026-09-29",
  to: "2026-10-28",
  timezone: "America/New_York",
  total_interactions: 12,
  successful_bookings: 3,
  faqs_answered: 5,
  open_escalations: 2,
};

export function row(over: Partial<InteractionRow> = {}): InteractionRow {
  return {
    id: "id-1",
    channel: "test_chat",
    started_at: "2026-10-21T18:30:00Z",
    ended_at: "2026-10-21T18:34:00Z",
    caller: null,
    intent: "BOOK_APPOINTMENT",
    summary: "The customer asked to book a haircut. It was booked for Friday.",
    summary_source: "ai",
    status: "completed",
    outcome: "booked",
    requires_human: false,
    needs_attention: false,
    escalation: null,
    ...over,
  };
}
