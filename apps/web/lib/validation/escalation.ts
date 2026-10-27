import { z } from "zod";

import type { EscalationSettings } from "@/lib/api/client";

/** Mirrors the API's bounds for `PUT /business/escalation-settings`. */
export const PHONE_PATTERN = /^\+[1-9][0-9]{6,14}$/;
export const MIN_THRESHOLD = 1;
export const MAX_THRESHOLD = 5;
export const MAX_KEYWORDS = 20;
export const MAX_KEYWORD_LENGTH = 40;

const PHONE_MESSAGE = "Use international format, such as +14155550123.";

/** Numbers stay strings in the form; `toApiSettings` converts them. */
export const escalationFormSchema = z
  .object({
    contact_phone: z.string(),
    sms_enabled: z.boolean(),
    clarification_threshold: z.string(),
    urgent_keywords: z.array(z.string().trim().min(1, "Keywords cannot be empty.").max(MAX_KEYWORD_LENGTH, `Keep each keyword to ${MAX_KEYWORD_LENGTH} characters or fewer.`)).max(MAX_KEYWORDS, `Add at most ${MAX_KEYWORDS} keywords.`),
  })
  .superRefine((values, ctx) => {
    const phone = values.contact_phone.trim();
    if (phone !== "" && !PHONE_PATTERN.test(phone)) {
      ctx.addIssue({ code: "custom", path: ["contact_phone"], message: PHONE_MESSAGE });
    } else if (phone === "" && values.sms_enabled) {
      ctx.addIssue({
        code: "custom",
        path: ["contact_phone"],
        message: "Add a phone number to receive text alerts, or turn text alerts off.",
      });
    }

    const attempts = values.clarification_threshold;
    if (!/^\d+$/.test(attempts) || Number(attempts) < MIN_THRESHOLD || Number(attempts) > MAX_THRESHOLD) {
      ctx.addIssue({
        code: "custom",
        path: ["clarification_threshold"],
        message: `Choose a number from ${MIN_THRESHOLD} to ${MAX_THRESHOLD}.`,
      });
    }

    const seen = new Set<string>();
    for (const keyword of values.urgent_keywords) {
      const key = keyword.trim().toLowerCase();
      if (seen.has(key)) {
        ctx.addIssue({ code: "custom", path: ["urgent_keywords"], message: "Each keyword can only be listed once." });
        break;
      }
      seen.add(key);
    }
  });

export type EscalationFormValues = z.infer<typeof escalationFormSchema>;

/**
 * Checks one keyword before it joins the list. Returns the trimmed keyword, or
 * an error message. Uses the same bounds as the schema.
 */
export function checkKeyword(raw: string, existing: readonly string[]): { ok: true; keyword: string } | { ok: false; message: string } {
  const keyword = raw.trim();
  if (keyword === "") return { ok: false, message: "Type a keyword first." };
  if (keyword.length > MAX_KEYWORD_LENGTH) {
    return { ok: false, message: `Keep each keyword to ${MAX_KEYWORD_LENGTH} characters or fewer.` };
  }
  if (existing.some((item) => item.trim().toLowerCase() === keyword.toLowerCase())) {
    return { ok: false, message: "That keyword is already in the list." };
  }
  if (existing.length >= MAX_KEYWORDS) {
    return { ok: false, message: `Add at most ${MAX_KEYWORDS} keywords.` };
  }
  return { ok: true, keyword };
}

/** API settings -> form values. */
export function fromApiSettings(settings: EscalationSettings): EscalationFormValues {
  return {
    contact_phone: settings.contact_phone ?? "",
    sms_enabled: settings.sms_enabled,
    clarification_threshold: String(settings.clarification_threshold),
    urgent_keywords: [...settings.urgent_keywords],
  };
}

/** Validated form values -> the body of the `PUT`. A blank phone is `null`. */
export function toApiSettings(values: EscalationFormValues): EscalationSettings {
  const phone = values.contact_phone.trim();
  return {
    contact_phone: phone === "" ? null : phone,
    sms_enabled: values.sms_enabled,
    clarification_threshold: Number(values.clarification_threshold),
    urgent_keywords: values.urgent_keywords.map((keyword) => keyword.trim()),
  };
}
