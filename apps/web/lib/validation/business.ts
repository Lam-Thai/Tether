import { z } from "zod";

/**
 * The full list of IANA zone names this runtime recognizes, used both to
 * populate the onboarding form's timezone `<select>` and to validate the
 * chosen value — available in all modern browsers and Node 18+.
 */
export const TIMEZONE_OPTIONS = Intl.supportedValuesOf("timeZone");

const VALID_TIMEZONES = new Set(TIMEZONE_OPTIONS);

/**
 * The single source of truth for "is this business form submittable" — the
 * onboarding form's `zodResolver` validates against this schema, and the
 * same schema's validated output is sent as the `POST /business` body, so
 * there's no second, hand-rolled set of validation rules to keep in sync.
 */
export const businessInputSchema = z.object({
  name: z.string().trim().min(1, "Business name is required."),
  industry: z.string().trim().min(1, "Industry is required."),
  address: z.string().trim().min(1, "Address is required."),
  timezone: z
    .string()
    .min(1, "Choose a timezone.")
    .refine((value) => VALID_TIMEZONES.has(value), {
      message: "Choose a valid timezone from the list.",
    }),
  phone: z.string().trim().optional(),
  contact_email: z
    .string()
    .trim()
    .optional()
    .refine((value) => !value || z.string().email().safeParse(value).success, {
      message: "Enter a valid email address.",
    }),
});

export type BusinessFormValues = z.infer<typeof businessInputSchema>;

/** Currencies the API accepts (ISO 4217) with their minor-unit digits. Mirrors `apps/api`. */
export const CURRENCIES = {
  USD: 2,
  CAD: 2,
  EUR: 2,
  GBP: 2,
  AUD: 2,
  NZD: 2,
  CHF: 2,
  SEK: 2,
  NOK: 2,
  DKK: 2,
  SGD: 2,
  HKD: 2,
  INR: 2,
  MXN: 2,
  BRL: 2,
  ZAR: 2,
  PHP: 2,
  THB: 2,
  CNY: 2,
  JPY: 0,
  KRW: 0,
  VND: 0,
} as const satisfies Record<string, 0 | 2>;

export const CURRENCY_CODES = Object.keys(CURRENCIES);

/** Minor-unit digits for a currency; unknown codes fall back to 2. */
export function currencyDigits(code: string): number {
  return (CURRENCIES as Record<string, number>)[code] ?? 2;
}

export const BUSINESS_LIMITS = {
  name: 120,
  industry: 80,
  address: 300,
  phone: 40,
  contactEmail: 254,
} as const;

/** The runtime's list can omit valid zones such as "UTC", so ask the runtime directly. */
function isKnownTimezone(value: string): boolean {
  if (VALID_TIMEZONES.has(value)) return true;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

const tooLong = (max: number) => `Use ${max} characters or fewer.`;

/**
 * The Agent Builder "Business information" form. Mirrors `PATCH /business/{id}`:
 * name / industry / address are required, phone and email may be blank (which
 * clears them), timezone and currency must come from the lists.
 */
export const businessInfoSchema = z.object({
  name: z.string().trim().min(1, "Business name is required.").max(BUSINESS_LIMITS.name, tooLong(BUSINESS_LIMITS.name)),
  industry: z.string().trim().min(1, "Industry is required.").max(BUSINESS_LIMITS.industry, tooLong(BUSINESS_LIMITS.industry)),
  address: z.string().trim().min(1, "Address is required.").max(BUSINESS_LIMITS.address, tooLong(BUSINESS_LIMITS.address)),
  timezone: z
    .string()
    .min(1, "Choose a timezone.")
    .refine(isKnownTimezone, { message: "Choose a valid timezone from the list." }),
  phone: z.string().trim().max(BUSINESS_LIMITS.phone, tooLong(BUSINESS_LIMITS.phone)),
  contact_email: z
    .string()
    .trim()
    .max(BUSINESS_LIMITS.contactEmail, tooLong(BUSINESS_LIMITS.contactEmail))
    .refine((value) => !value || z.string().email().safeParse(value).success, {
      message: "Enter a valid email address.",
    }),
  currency: z.string().refine((value) => value in CURRENCIES, { message: "Choose a currency from the list." }),
});

export type BusinessInfoValues = z.infer<typeof businessInfoSchema>;
