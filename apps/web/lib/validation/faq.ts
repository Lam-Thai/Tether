import { z } from "zod";

export const FAQ_LIMITS = {
  question: 300,
  answer: 1000,
  category: 60,
} as const;

/**
 * Mirrors the API's FAQ limits (question 1-300, answer 1-1000, category 0-60,
 * all trimmed; a blank category means none). The dialog validates against this
 * and its output is the `POST /faqs` body (or, changed fields only, `PATCH`).
 */
export const faqFormSchema = z.object({
  question: z
    .string()
    .trim()
    .min(1, "Question is required.")
    .max(FAQ_LIMITS.question, `Use ${FAQ_LIMITS.question} characters or fewer.`),
  answer: z
    .string()
    .trim()
    .min(1, "Answer is required.")
    .max(FAQ_LIMITS.answer, `Use ${FAQ_LIMITS.answer} characters or fewer.`),
  category: z
    .string()
    .trim()
    .max(FAQ_LIMITS.category, `Use ${FAQ_LIMITS.category} characters or fewer.`),
  active: z.boolean(),
});

export type FaqFormValues = z.infer<typeof faqFormSchema>;
