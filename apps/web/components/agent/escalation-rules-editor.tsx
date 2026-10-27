"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuth } from "@clerk/nextjs";
import { CircleCheck, X } from "lucide-react";

import {
  updateEscalationSettings,
  type EscalationSettingsRead,
  type EscalationSettingsResult,
} from "@/lib/api/client";
import {
  MAX_KEYWORDS,
  MAX_KEYWORD_LENGTH,
  MAX_THRESHOLD,
  MIN_THRESHOLD,
  checkKeyword,
  escalationFormSchema,
  fromApiSettings,
  toApiSettings,
  type EscalationFormValues,
} from "@/lib/validation/escalation";
import { SESSION_EXPIRED, SectionLoadFailure, Switch, commonFailure } from "@/components/agent/section-shared";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

type SaveState = { kind: "idle" } | { kind: "saved" } | { kind: "error"; message: ReactNode };

const THRESHOLDS = Array.from({ length: MAX_THRESHOLD - MIN_THRESHOLD + 1 }, (_, i) => MIN_THRESHOLD + i);

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      <p id={`${id}-hint`} className="text-sm text-muted-foreground">
        {hint}
      </p>
      {error && (
        <div id={`${id}-error`}>
          <FieldError>{error}</FieldError>
        </div>
      )}
    </div>
  );
}

function EscalationRulesPanel({ initial }: { initial: EscalationSettingsRead }) {
  const { getToken } = useAuth();
  const [customized, setCustomized] = useState(initial.customized);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    control,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<EscalationFormValues>({
    resolver: zodResolver(escalationFormSchema),
    defaultValues: fromApiSettings(initial),
  });

  const smsEnabled = useWatch({ control, name: "sms_enabled" });
  const keywords = useWatch({ control, name: "urgent_keywords" });
  const clearSaved = () => {
    if (saveState.kind !== "idle") setSaveState({ kind: "idle" });
  };

  function addKeyword() {
    const checked = checkKeyword(draft, keywords);
    if (!checked.ok) {
      setDraftError(checked.message);
      return;
    }
    setValue("urgent_keywords", [...keywords, checked.keyword], { shouldDirty: true, shouldValidate: true });
    setDraft("");
    setDraftError(null);
    clearSaved();
  }

  function removeKeyword(keyword: string) {
    setValue(
      "urgent_keywords",
      keywords.filter((item) => item !== keyword),
      { shouldDirty: true, shouldValidate: true },
    );
    clearSaved();
  }

  function onDraftKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    // Enter adds the keyword; it must not submit the whole form.
    if (event.key === "Enter") {
      event.preventDefault();
      addKeyword();
    }
  }

  async function onSubmit(values: EscalationFormValues) {
    setSaveState({ kind: "idle" });

    const token = await getToken();
    if (!token) {
      setSaveState({ kind: "error", message: SESSION_EXPIRED });
      return;
    }

    const result = await updateEscalationSettings(token, toApiSettings(values));

    if (result.kind === "ok") {
      // Only now are they saved: adopt what the API returned as the new baseline.
      setCustomized(result.data.customized);
      reset(fromApiSettings(result.data));
      setSaveState({ kind: "saved" });
    } else if (result.kind === "validation" || result.kind === "contact_phone_required") {
      setSaveState({ kind: "error", message: result.message });
    } else if (result.kind === "unreachable") {
      setSaveState({ kind: "error", message: result.reason });
    } else {
      setSaveState({ kind: "error", message: commonFailure(result.kind) });
    }
  }

  const describe = (id: string, error?: string) => (error ? `${id}-hint ${id}-error` : `${id}-hint`);

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Escalation rules</CardTitle>
        <CardDescription>
          When the receptionist hands a customer to a person, and how you hear about it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-5"
          onSubmit={handleSubmit(onSubmit)}
          onChange={clearSaved}
          noValidate
        >
          {!initial.sms_configured && (
            <Callout tone="warning" compact headingLevel={3} title="Demo mode">
              Alerts are recorded in your inbox; SMS sending isn&apos;t set up, so nothing is texted.
            </Callout>
          )}
          {!customized && (
            <Callout tone="warning" compact headingLevel={3} title="Using defaults">
              You have not saved any escalation rules, so the standard ones shown below apply until you
              press Save escalation rules.
            </Callout>
          )}
          {saveState.kind === "error" && <FormError>{saveState.message}</FormError>}

          <div className="flex flex-col gap-1.5">
            <p id="escalation-sms-label" className="text-sm font-medium">
              Text me when a customer needs a person
            </p>
            <Switch
              checked={smsEnabled}
              pending={isSubmitting}
              label="Text me when a customer needs a person"
              onToggle={() => {
                setValue("sms_enabled", !smsEnabled, { shouldDirty: true, shouldValidate: true });
                clearSaved();
              }}
            />
            <p className="text-sm text-muted-foreground">
              One text per conversation, however many reasons it raises. If you are on a Twilio trial
              account, it can only text numbers you have verified with Twilio.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <Field
              id="escalation-phone"
              label="Contact phone"
              hint="International format, such as +14155550123. Required for text alerts."
              error={errors.contact_phone?.message}
            >
              <Input
                id="escalation-phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                placeholder="+14155550123"
                aria-invalid={errors.contact_phone ? true : undefined}
                aria-describedby={describe("escalation-phone", errors.contact_phone?.message)}
                {...register("contact_phone")}
              />
            </Field>

            <Field
              id="escalation-threshold"
              label="Clarification attempts"
              hint="How many times the receptionist can fail to understand before it hands over."
              error={errors.clarification_threshold?.message}
            >
              <Select
                id="escalation-threshold"
                aria-invalid={errors.clarification_threshold ? true : undefined}
                aria-describedby={describe("escalation-threshold", errors.clarification_threshold?.message)}
                {...register("clarification_threshold")}
              >
                {THRESHOLDS.map((count) => (
                  <option key={count} value={String(count)}>
                    {count}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="escalation-keyword">Urgent keywords</Label>
            <div className="flex gap-2">
              <Input
                id="escalation-keyword"
                value={draft}
                maxLength={MAX_KEYWORD_LENGTH + 20}
                placeholder="gas leak"
                aria-invalid={draftError ? true : undefined}
                aria-describedby={
                  draftError ? "escalation-keyword-hint escalation-keyword-error" : "escalation-keyword-hint"
                }
                onChange={(event) => {
                  setDraft(event.target.value);
                  setDraftError(null);
                }}
                onKeyDown={onDraftKeyDown}
              />
              <Button type="button" variant="outline" onClick={addKeyword}>
                Add keyword
              </Button>
            </div>
            <p id="escalation-keyword-hint" className="text-sm text-muted-foreground">
              A message containing one of these is treated as urgent. Up to {MAX_KEYWORDS}, each{" "}
              {MAX_KEYWORD_LENGTH} characters or fewer.
            </p>
            {draftError && (
              <div id="escalation-keyword-error">
                <FieldError>{draftError}</FieldError>
              </div>
            )}
            {errors.urgent_keywords && (
              <FieldError>
                {errors.urgent_keywords.message ??
                  errors.urgent_keywords.root?.message ??
                  "Check the keyword list."}
              </FieldError>
            )}
            {keywords.length === 0 ? (
              <p className="text-sm text-muted-foreground">No urgent keywords yet.</p>
            ) : (
              <ul aria-label="Added keywords" className="flex flex-wrap gap-2">
                {keywords.map((keyword) => (
                  <li
                    key={keyword}
                    className="inline-flex items-center gap-1 rounded-sm border border-border bg-card py-1 pr-1 pl-2.5 text-sm"
                  >
                    <span>{keyword}</span>
                    <button
                      type="button"
                      onClick={() => removeKeyword(keyword)}
                      aria-label={`Remove ${keyword}`}
                      className="inline-flex size-7 items-center justify-center rounded-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    >
                      <X aria-hidden className="size-4" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex items-center gap-4">
            <Button type="submit" disabled={(customized && !isDirty) || isSubmitting}>
              {isSubmitting ? "Saving…" : "Save escalation rules"}
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

/** Agent Builder "Escalation rules". Switches on the `GET /business/escalation-settings` result so a failed load never breaks the page. */
export function EscalationRulesEditor({ result }: { result: EscalationSettingsResult }) {
  if (result.kind === "ok") return <EscalationRulesPanel initial={result.data} />;
  return (
    <SectionLoadFailure
      kind={result.kind}
      reason={result.kind === "unreachable" ? result.reason : undefined}
      noun="escalation rules"
    />
  );
}
