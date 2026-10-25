import { Ban, CalendarCheck, CircleCheck, Clock, Flag, TriangleAlert, UserRound } from "lucide-react";

import type { InteractionOutcome, InteractionStatus } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { outcomeLabel, statusLabel } from "./labels";

const OUTCOME_VIEW = {
  booked: { tone: "success", Icon: CalendarCheck },
  answered: { tone: "neutral", Icon: CircleCheck },
  escalated: { tone: "warning", Icon: Flag },
  failed: { tone: "danger", Icon: TriangleAlert },
  abandoned: { tone: "neutral", Icon: Ban },
} as const satisfies Record<InteractionOutcome, { tone: "neutral" | "success" | "warning" | "danger"; Icon: typeof Ban }>;

const STATUS_VIEW = {
  in_progress: { tone: "warning", Icon: Clock },
  completed: { tone: "neutral", Icon: CircleCheck },
  failed: { tone: "danger", Icon: TriangleAlert },
  abandoned: { tone: "neutral", Icon: Ban },
} as const satisfies Record<InteractionStatus, { tone: "neutral" | "success" | "warning" | "danger"; Icon: typeof Ban }>;

/** Whether a status needs its own mark: completed is the normal case, and failed/abandoned outcomes already say it. */
export function needsStatusMark(status: InteractionStatus, outcome: InteractionOutcome | null): boolean {
  if (status === "completed") return false;
  if (status === "failed" && outcome === "failed") return false;
  if (status === "abandoned" && outcome === "abandoned") return false;
  return true;
}

export function OutcomeBadge({ outcome }: { outcome: InteractionOutcome }) {
  const { tone, Icon } = OUTCOME_VIEW[outcome];
  return (
    <Badge tone={tone}>
      <Icon aria-hidden />
      {outcomeLabel(outcome)}
    </Badge>
  );
}

export function StatusBadge({ status }: { status: InteractionStatus }) {
  const { tone, Icon } = STATUS_VIEW[status];
  return (
    <Badge tone={tone}>
      <Icon aria-hidden />
      {statusLabel(status)}
    </Badge>
  );
}

export function EscalationBadge() {
  return (
    <Badge tone="warning">
      <UserRound aria-hidden />
      Needs a person
    </Badge>
  );
}
