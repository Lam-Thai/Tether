import { z } from "zod";

export const AGENT_LIMITS = {
  name: 60,
  greeting: 300,
  customInstructions: 2000,
  message: 1000,
} as const;

export const TONE_OPTIONS = [
  { value: "friendly", label: "Friendly", description: "Warm and welcoming, like a good neighbor." },
  { value: "professional", label: "Professional", description: "Polite, polished and businesslike." },
  { value: "concise", label: "Concise", description: "Short, direct answers with no extras." },
] as const;

/**
 * Mirrors the API's `AgentUpdate` limits (name 1-60, greeting 1-300,
 * custom instructions 0-2000, all trimmed). The form validates against this
 * and the changed fields of its output are the `PATCH /agent` body.
 */
export const agentFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Receptionist name is required.")
    .max(AGENT_LIMITS.name, `Use ${AGENT_LIMITS.name} characters or fewer.`),
  greeting: z
    .string()
    .trim()
    .min(1, "Greeting is required.")
    .max(AGENT_LIMITS.greeting, `Use ${AGENT_LIMITS.greeting} characters or fewer.`),
  tone: z.enum(["friendly", "professional", "concise"]),
  // The allowed ids come from `GET /agent/voices`; the API rejects unknown ones.
  voice: z.string().trim().min(1, "Choose a voice."),
  custom_instructions: z
    .string()
    .trim()
    .max(AGENT_LIMITS.customInstructions, `Use ${AGENT_LIMITS.customInstructions} characters or fewer.`),
});

export type AgentFormValues = z.infer<typeof agentFormSchema>;

/** One chat message: 1-1000 characters after trimming, same as `POST /agent/test`. */
export const testMessageSchema = z
  .string()
  .trim()
  .min(1)
  .max(AGENT_LIMITS.message);
