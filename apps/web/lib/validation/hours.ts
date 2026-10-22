import { z } from "zod";

import type { DayHours } from "@/lib/api/client";

/** Monday first, matching the API's `day_of_week` (0 = Monday). */
export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

export const DEFAULT_OPEN = "09:00";
export const DEFAULT_CLOSE = "17:00";

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

export const dayFormSchema = z.object({
  is_closed: z.boolean(),
  opens_at: z.string(),
  closes_at: z.string(),
});

/**
 * Mirrors the API's rules for `PUT /business/hours`: exactly seven days; a
 * closed day carries no times; an open day needs both and opens < closes on
 * the same day (no overnight). Times are `HH:MM` in the form.
 */
export const hoursFormSchema = z.object({
  days: z.array(dayFormSchema).length(7).superRefine((days, ctx) => {
    days.forEach((day, index) => {
      if (day.is_closed) return;
      if (!day.opens_at || !day.closes_at) {
        ctx.addIssue({
          code: "custom",
          path: [index, "closes_at"],
          message: "Set both opening and closing times, or mark the day closed.",
        });
      } else if (!TIME.test(day.opens_at) || !TIME.test(day.closes_at)) {
        ctx.addIssue({ code: "custom", path: [index, "closes_at"], message: "Enter times as HH:MM." });
      } else if (day.opens_at >= day.closes_at) {
        ctx.addIssue({
          code: "custom",
          path: [index, "closes_at"],
          message: "Closing time must be after opening time.",
        });
      }
    });
  }),
});

export type HoursFormValues = z.infer<typeof hoursFormSchema>;
export type DayFormValues = z.infer<typeof dayFormSchema>;

/** The unsaved starting point when the owner has never set hours: Mon-Fri 09:00-17:00. */
export function defaultDraft(): HoursFormValues {
  return {
    days: WEEKDAYS.map((_, index) =>
      index < 5
        ? { is_closed: false, opens_at: DEFAULT_OPEN, closes_at: DEFAULT_CLOSE }
        : { is_closed: true, opens_at: "", closes_at: "" },
    ),
  };
}

/** API week -> form values, indexed by `day_of_week`. A missing day is treated as closed. */
export function fromApiDays(days: DayHours[]): HoursFormValues {
  return {
    days: WEEKDAYS.map((_, index) => {
      const day = days.find((d) => d.day_of_week === index);
      if (!day || day.is_closed || !day.opens_at || !day.closes_at) {
        return { is_closed: true, opens_at: "", closes_at: "" };
      }
      return { is_closed: false, opens_at: day.opens_at.slice(0, 5), closes_at: day.closes_at.slice(0, 5) };
    }),
  };
}

/** Validated form values -> the seven-day `PUT` body. */
export function toApiDays(values: HoursFormValues): DayHours[] {
  return values.days.map((day, index) =>
    day.is_closed
      ? { day_of_week: index, is_closed: true, opens_at: null, closes_at: null }
      : { day_of_week: index, is_closed: false, opens_at: `${day.opens_at}:00`, closes_at: `${day.closes_at}:00` },
  );
}
