import Link from "next/link";

import type {
  AgentResult,
  BookingRulesResult,
  BusinessHoursResult,
  BusinessResult,
  EscalationSettingsResult,
  FaqListResult,
  ServiceListResult,
  VoiceListResult,
} from "@/lib/api/client";
import { AgentBuilder } from "@/components/agent/agent-builder";
import { BookingRulesEditor } from "@/components/agent/booking-rules-editor";
import { BusinessInfoForm } from "@/components/agent/business-info-form";
import { EscalationRulesEditor } from "@/components/agent/escalation-rules-editor";
import { FaqManager } from "@/components/agent/faq-manager";
import { HoursEditor } from "@/components/agent/hours-editor";
import { ServicesManager } from "@/components/agent/services-manager";
import { TestChat, type ChatLoad } from "@/components/agent/test-chat";
import { buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";

/**
 * Presentational view of `/dashboard/agent`: switches exhaustively on the
 * `GET /agent` result. The chat's own load state travels in `chat`. The builder
 * column follows the PRD order: identity and personality, business information,
 * FAQs, services, business hours; each section handles its own load failure.
 */
export function AgentWorkspace({
  agent,
  chat,
  faqs,
  business,
  services,
  hours,
  bookingRules,
  escalationSettings,
  voices,
}: {
  agent: AgentResult;
  chat: ChatLoad;
  faqs: FaqListResult;
  business: BusinessResult;
  services: ServiceListResult;
  hours: BusinessHoursResult;
  bookingRules: BookingRulesResult;
  escalationSettings: EscalationSettingsResult;
  voices: VoiceListResult;
}) {
  switch (agent.kind) {
    case "ok":
      return (
        <div className="flex w-full max-w-[1280px] flex-col gap-6">
          <h1 className="text-h1 font-bold tracking-tight uppercase">Agent</h1>
          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <div className="flex min-w-0 flex-col gap-6">
              <AgentBuilder agent={agent.data} voices={voices} />
              <BusinessInfoForm result={business} />
              <FaqManager result={faqs} />
              <ServicesManager result={services} />
              <HoursEditor result={hours} />
              <BookingRulesEditor result={bookingRules} />
              <EscalationRulesEditor result={escalationSettings} />
            </div>
            <div className="min-w-0 lg:sticky lg:top-6">
              <TestChat agentName={agent.data.name} chat={chat} />
            </div>
          </div>
        </div>
      );
    case "subscription_required":
      return (
        <div className="max-w-3xl">
          <Callout
            tone="warning"
            title="Plan required"
            headingLevel={1}
            action={
              <Link href="/billing/plan" className={buttonVariants({ size: "sm" })}>
                Choose plan
              </Link>
            }
          >
            Choose a plan to configure and test your receptionist.
          </Callout>
        </div>
      );
    case "not_found":
      return (
        <div className="max-w-3xl">
          <Callout tone="warning" title="Business needed" headingLevel={1}>
            Set up your business before configuring the receptionist.
          </Callout>
        </div>
      );
    case "unauthorized":
      return (
        <div className="max-w-3xl">
          <Callout tone="danger" title="Signed out" headingLevel={1}>
            Your session has expired. <Link href="/sign-in" className="underline">Sign in again</Link>.
          </Callout>
        </div>
      );
    case "unreachable":
      return (
        <div className="max-w-3xl">
          <Callout tone="danger" title="Can't load your agent" headingLevel={1}>
            {agent.reason}
          </Callout>
        </div>
      );
  }
}
