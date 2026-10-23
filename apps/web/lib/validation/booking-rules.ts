import { z } from "zod";

import type { BookingRules } from "@/lib/api/client";

/** Mirrors the API's bounds for `PUT /business/booking-rules`. */
export const SLOT_INTERVALS = [5, 10, 15, 20, 30, 45, 60] as const;
export const MAX_NOTICE_MINUTES = 20160;
export const MAX_DAYS_AHEAD = 365;
export const MAX_BUFFER_MINUTES = 240;

export type NoticeUnit = "minutes" | "hours";

/** Numbers stay strings in the form (as hours and prices do); `toApiRules` converts them. */
export const bookingRulesFormSchema = z
  .object({
    slot_interval_minutes: z.string(),
    min_notice_value: z.string(),
    min_notice_unit: z.enum(["minutes", "hours"]),
    max_days_ahead: z.string(),
    buffer_minutes: z.string(),
  })
  .superRefine((values, ctx) => {
    const whole = /^\d+$/;

    const interval = Number(values.slot_interval_minutes);
    if (!whole.test(values.slot_interval_minutes) || interval < 5 || interval > 240 || interval % 5 !== 0) {
      ctx.addIssue({ code: "custom", path: ["slot_interval_minutes"], message: "Choose a slot length between 5 and 240 minutes, in steps of 5." });
    }

    if (!whole.test(values.min_notice_value)) {
      ctx.addIssue({ code: "custom", path: ["min_notice_value"], message: "Enter a whole number, 0 or more." });
    } else {
      const minutes = Number(values.min_notice_value) * (values.min_notice_unit === "hours" ? 60 : 1);
      if (minutes > MAX_NOTICE_MINUTES) {
        ctx.addIssue({
          code: "custom",
          path: ["min_notice_value"],
          message: "Notice can be at most 14 days (336 hours).",
        });
      }
    }

    if (!whole.test(values.max_days_ahead) || Number(values.max_days_ahead) < 1) {
      ctx.addIssue({ code: "custom", path: ["max_days_ahead"], message: "Enter a whole number of days, 1 or more." });
    } else if (Number(values.max_days_ahead) > MAX_DAYS_AHEAD) {
      ctx.addIssue({
        code: "custom",
        path: ["max_days_ahead"],
        message: `Customers can book at most ${MAX_DAYS_AHEAD} days ahead.`,
      });
    }

    if (!whole.test(values.buffer_minutes)) {
      ctx.addIssue({ code: "custom", path: ["buffer_minutes"], message: "Enter a whole number of minutes, 0 or more." });
    } else if (Number(values.buffer_minutes) > MAX_BUFFER_MINUTES) {
      ctx.addIssue({
        code: "custom",
        path: ["buffer_minutes"],
        message: `The buffer can be at most ${MAX_BUFFER_MINUTES} minutes.`,
      });
    } else if (Number(values.buffer_minutes) % 5 !== 0) {
      ctx.addIssue({ code: "custom", path: ["buffer_minutes"], message: "Use a multiple of 5 minutes." });
    }
  });

export type BookingRulesFormValues = z.infer<typeof bookingRulesFormSchema>;

/** API rules -> form values. Whole hours of notice are shown in hours. */
export function fromApiRules(rules: BookingRules): BookingRulesFormValues {
  const hours = rules.min_notice_minutes > 0 && rules.min_notice_minutes % 60 === 0;
  return {
    slot_interval_minutes: String(rules.slot_interval_minutes),
    min_notice_value: String(hours ? rules.min_notice_minutes / 60 : rules.min_notice_minutes),
    min_notice_unit: hours ? "hours" : "minutes",
    max_days_ahead: String(rules.max_days_ahead),
    buffer_minutes: String(rules.buffer_minutes),
  };
}

/** Validated form values -> the four integers the `PUT` expects. */
export function toApiRules(values: BookingRulesFormValues): BookingRules {
  return {
    slot_interval_minutes: Number(values.slot_interval_minutes),
    min_notice_minutes: Number(values.min_notice_value) * (values.min_notice_unit === "hours" ? 60 : 1),
    max_days_ahead: Number(values.max_days_ahead),
    buffer_minutes: Number(values.buffer_minutes),
  };
}
