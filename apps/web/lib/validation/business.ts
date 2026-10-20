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
