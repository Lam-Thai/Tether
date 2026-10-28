"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuth } from "@clerk/nextjs";
import { CircleCheck } from "lucide-react";

import {
  updateAgent,
  type AgentRead,
  type AgentUpdate,
  type VoiceListResult,
} from "@/lib/api/client";
import { VoiceField } from "@/components/agent/voice-field";
import {
  AGENT_LIMITS,
  TONE_OPTIONS,
  agentFormSchema,
  type AgentFormValues,
} from "@/lib/validation/agent";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input, controlClasses } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

const SESSION_EXPIRED = "Your session has expired. Please sign in again.";

function toFormValues(agent: AgentRead): AgentFormValues {
  return {
    name: agent.name,
    greeting: agent.greeting,
    tone: agent.tone,
    voice: agent.voice,
    custom_instructions: agent.custom_instructions,
  };
}

/** Only the fields whose validated value differs from what was last saved. */
export function changedFields(values: AgentFormValues, saved: AgentRead): AgentUpdate {
  const patch: AgentUpdate = {};
  if (values.name !== saved.name) patch.name = values.name;
  if (values.greeting !== saved.greeting) patch.greeting = values.greeting;
  if (values.tone !== saved.tone) patch.tone = values.tone;
  if (values.voice !== saved.voice) patch.voice = values.voice;
  if (values.custom_instructions !== saved.custom_instructions) {
    patch.custom_instructions = values.custom_instructions;
  }
  return patch;
}

type SaveState =
  | { kind: "idle" }
  | { kind: "saved" }
  | { kind: "error"; message: string }
  | { kind: "subscription_required" };

export function AgentBuilder({ agent, voices }: { agent: AgentRead; voices: VoiceListResult }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [saved, setSaved] = useState<AgentRead>(agent);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  const {
    register,
    handleSubmit,
    reset,
    setError,
    control,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<AgentFormValues>({
    resolver: zodResolver(agentFormSchema),
    defaultValues: toFormValues(agent),
  });

  const selectedVoice = useWatch({ control, name: "voice" }) ?? agent.voice;
  const draftGreeting = useWatch({ control, name: "greeting" }) ?? "";
  const instructionsLength = (useWatch({ control, name: "custom_instructions" }) ?? "").length;

  async function onSubmit(values: AgentFormValues) {
    setSaveState({ kind: "idle" });

    const token = await getToken();
    if (!token) {
      setSaveState({ kind: "error", message: SESSION_EXPIRED });
      return;
    }

    const patch = changedFields(values, saved);
    const result = await updateAgent(token, patch);

    switch (result.kind) {
      case "ok":
        // Only now is it saved: adopt what the API returned as the new baseline.
        setSaved(result.data);
        reset(toFormValues(result.data));
        setSaveState({ kind: "saved" });
        router.refresh();
        return;
      case "validation": {
        let placed = false;
        for (const [field, message] of Object.entries(result.fieldErrors)) {
          if (field in toFormValues(saved)) {
            setError(field as keyof AgentFormValues, { message });
            placed = true;
          }
        }
        if (!placed) setSaveState({ kind: "error", message: result.message });
        return;
      }
      case "subscription_required":
        setSaveState({ kind: "subscription_required" });
        return;
      case "unauthorized":
        setSaveState({ kind: "error", message: SESSION_EXPIRED });
        return;
      case "not_found":
        setSaveState({ kind: "error", message: "Set up your business before editing the receptionist." });
        return;
      case "unreachable":
        setSaveState({ kind: "error", message: result.reason });
        return;
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Your receptionist</CardTitle>
        <CardDescription>
          Choose how your receptionist introduces itself and how it sounds. Changes apply to the
          next message.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-8"
          onSubmit={handleSubmit(onSubmit)}
          onChange={() => {
            if (saveState.kind !== "idle") setSaveState({ kind: "idle" });
          }}
          noValidate
        >
          {saveState.kind === "error" && <FormError>{saveState.message}</FormError>}
          {saveState.kind === "subscription_required" && (
            <FormError>
              Your plan is not active.{" "}
              <Link href="/billing/plan" className="font-medium underline">
                Choose a plan
              </Link>
            </FormError>
          )}

          <fieldset className="flex flex-col gap-4">
            <legend className="mb-4 text-lg font-bold tracking-tight uppercase">1. Identity</legend>

            <div className="flex flex-col gap-2">
              <Label htmlFor="agent-name">Receptionist name</Label>
              <Input
                id="agent-name"
                autoComplete="off"
                aria-invalid={errors.name ? true : undefined}
                aria-describedby={errors.name ? "agent-name-error" : undefined}
                {...register("name")}
              />
              {errors.name && (
                <div id="agent-name-error">
                  <FieldError>{errors.name.message}</FieldError>
                </div>
              )}
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="agent-greeting">Greeting</Label>
              <textarea
                id="agent-greeting"
                rows={3}
                className={cn(controlClasses, "h-auto resize-y")}
                aria-invalid={errors.greeting ? true : undefined}
                aria-describedby={errors.greeting ? "agent-greeting-help agent-greeting-error" : "agent-greeting-help"}
                {...register("greeting")}
              />
              <p id="agent-greeting-help" className="text-sm text-muted-foreground">
                The first message a customer sees. It is sent exactly as written.
              </p>
              {errors.greeting && (
                <div id="agent-greeting-error">
                  <FieldError>{errors.greeting.message}</FieldError>
                </div>
              )}
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-4">
            <legend className="mb-4 text-lg font-bold tracking-tight uppercase">2. Personality</legend>

            <div role="radiogroup" aria-label="Tone" className="grid grid-cols-1 gap-3">
              {TONE_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className="flex cursor-pointer items-start gap-3 rounded-md border border-input bg-card p-3 has-[:checked]:border-foreground has-[:checked]:bg-muted has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring"
                >
                  <input
                    type="radio"
                    value={option.value}
                    className="mt-1 size-4 accent-foreground"
                    {...register("tone")}
                  />
                  <span className="flex flex-col">
                    <span className="text-base font-medium">{option.label}</span>
                    <span className="text-sm text-muted-foreground">{option.description}</span>
                  </span>
                </label>
              ))}
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="agent-instructions">Custom instructions (optional)</Label>
              <textarea
                id="agent-instructions"
                rows={6}
                className={cn(controlClasses, "h-auto resize-y")}
                aria-invalid={errors.custom_instructions ? true : undefined}
                aria-describedby={
                  errors.custom_instructions
                    ? "agent-instructions-help agent-instructions-error"
                    : "agent-instructions-help"
                }
                {...register("custom_instructions")}
              />
              <div className="flex items-start justify-between gap-4">
                <p id="agent-instructions-help" className="text-sm text-muted-foreground">
                  Instructions shape the tone and content of replies, but cannot override
                  Tether&apos;s safety rules.
                </p>
                <p
                  className={cn(
                    "shrink-0 font-mono text-sm tabular-nums",
                    instructionsLength > AGENT_LIMITS.customInstructions
                      ? "text-danger-foreground"
                      : "text-muted-foreground",
                  )}
                >
                  {instructionsLength}/{AGENT_LIMITS.customInstructions}
                </p>
              </div>
              {errors.custom_instructions && (
                <div id="agent-instructions-error">
                  <FieldError>{errors.custom_instructions.message}</FieldError>
                </div>
              )}
            </div>

            <VoiceField
              voices={voices}
              selectProps={register("voice")}
              selected={selectedVoice}
              greetingChanged={draftGreeting.trim() !== saved.greeting}
            />
          </fieldset>

          <div className="flex items-center gap-4">
            <Button type="submit" disabled={!isDirty || isSubmitting}>
              {isSubmitting ? "Saving…" : "Save changes"}
            </Button>
            <p role="status" className="flex items-center gap-1.5 text-sm text-foreground">
              {saveState.kind === "saved" && (
                <>
                  <CircleCheck aria-hidden className="size-4" />
                  Saved
                </>
              )}
            </p>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
