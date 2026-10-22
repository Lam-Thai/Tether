import { z } from "zod";

import { currencyDigits } from "@/lib/validation/business";

export const SERVICE_LIMITS = {
  name: 80,
  description: 500,
  durationMax: 1440,
} as const;

/** Whole numbers, optionally with up to `digits` decimals ("45", "45.5", "45.50"). */
export function priceShape(digits: number): RegExp {
  return digits === 0 ? /^[0-9]+$/ : new RegExp("^[0-9]+([.][0-9]{1," + digits + "})?$");
}

/**
 * Mirrors the API's service limits (name 1-80, description 0-500, duration 1-1440
 * whole minutes, price >= 0 with at most the currency's minor digits; a blank
 * price means no listed price). Number-like fields stay strings in the form;
 * `toServiceBody` converts the validated values into the request body.
 */
export function serviceFormSchema(currency: string) {
  const digits = currencyDigits(currency);
  const shape = priceShape(digits);
  return z.object({
    name: z
      .string()
      .trim()
      .min(1, "Name is required.")
      .max(SERVICE_LIMITS.name, `Use ${SERVICE_LIMITS.name} characters or fewer.`),
    description: z
      .string()
      .trim()
      .max(SERVICE_LIMITS.description, `Use ${SERVICE_LIMITS.description} characters or fewer.`),
    duration_minutes: z
      .string()
      .trim()
      .min(1, "Duration is required.")
      .refine((v) => /^\d+$/.test(v), { message: "Enter a whole number of minutes." })
      .refine((v) => !/^\d+$/.test(v) || (Number(v) >= 1 && Number(v) <= SERVICE_LIMITS.durationMax), {
        message: `Use 1 to ${SERVICE_LIMITS.durationMax} minutes.`,
      }),
    price: z
      .string()
      .trim()
      .refine((v) => v === "" || shape.test(v), {
        message:
          digits === 0
            ? `Enter a whole amount in ${currency}, or leave blank.`
            : `Enter an amount like 45 or 45.50 in ${currency}, or leave blank.`,
      }),
    active: z.boolean(),
  });
}

export type ServiceFormValues = z.infer<ReturnType<typeof serviceFormSchema>>;

export interface ServiceBody {
  name: string;
  description: string | null;
  duration_minutes: number;
  price: string | null;
  active: boolean;
}

/** Validated form values -> `POST /services` body. */
export function toServiceBody(values: ServiceFormValues): ServiceBody {
  return {
    name: values.name,
    description: values.description || null,
    duration_minutes: Number(values.duration_minutes),
    price: values.price || null,
    active: values.active,
  };
}
