"use client";

import { useState, type ReactNode } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuth } from "@clerk/nextjs";
import { CircleCheck } from "lucide-react";

import { putBusinessHours, type BusinessHours, type BusinessHoursResult } from "@/lib/api/client";
import {
  DEFAULT_CLOSE,
  DEFAULT_OPEN,
  WEEKDAYS,
  defaultDraft,
  fromApiDays,
  hoursFormSchema,
  toApiDays,
  type HoursFormValues,
} from "@/lib/validation/hours";
import {
  SESSION_EXPIRED,
  SectionLoadFailure,
  Switch,
  commonFailure,
} from "@/components/agent/section-shared";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

type SaveState = { kind: "idle" } | { kind: "saved" } | { kind: "error"; message: ReactNode };

function HoursPanel({ initial }: { initial: BusinessHours }) {
  const { getToken } = useAuth();
  const [configured, setConfigured] = useState(initial.configured);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    clearErrors,
    control,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<HoursFormValues>({
    resolver: zodResolver(hoursFormSchema),
    defaultValues: initial.configured ? fromApiDays(initial.days) : defaultDraft(),
  });

  const days = useWatch({ control, name: "days" });

  function toggleClosed(index: number) {
    const closed = !days[index]?.is_closed;
    setSaveState({ kind: "idle" });
    clearErrors(`days.${index}.closes_at`);
    setValue(`days.${index}.is_closed`, closed, { shouldDirty: true });
    // A closed day carries no times; reopening starts from the usual hours.
    setValue(`days.${index}.opens_at`, closed ? "" : DEFAULT_OPEN, { shouldDirty: true });
    setValue(`days.${index}.closes_at`, closed ? "" : DEFAULT_CLOSE, { shouldDirty: true });
  }

  async function onSubmit(values: HoursFormValues) {
    setSaveState({ kind: "idle" });

    const token = await getToken();
    if (!token) {
      setSaveState({ kind: "error", message: SESSION_EXPIRED });
      return;
    }

    const result = await putBusinessHours(token, toApiDays(values));

    if (result.kind === "ok") {
      // Only now is it saved: adopt what the API returned as the new baseline.
      setConfigured(result.data.configured);
      reset(fromApiDays(result.data.days));
      setSaveState({ kind: "saved" });
    } else if (result.kind === "validation") {
      setSaveState({ kind: "error", message: result.message });
    } else if (result.kind === "unreachable") {
      setSaveState({ kind: "error", message: result.reason });
    } else {
      setSaveState({ kind: "error", message: commonFailure(result.kind) });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Business hours</CardTitle>
        <CardDescription>
          The receptionist tells customers when you are open. Mark a day closed if you are never
          open on it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-5"
          onSubmit={handleSubmit(onSubmit)}
          onChange={() => {
            if (saveState.kind === "saved") setSaveState({ kind: "idle" });
          }}
          noValidate
        >
          {!configured && (
            <Callout tone="warning" compact headingLevel={3} title="Not set yet">
              You have not saved any hours, so the receptionist can&apos;t say when you are open. The
              hours below are a starting point and are not saved until you press Save hours.
            </Callout>
          )}
          {saveState.kind === "error" && <FormError>{saveState.message}</FormError>}

          <p className="text-sm text-muted-foreground">
            Times are in <span className="font-mono text-foreground">{initial.timezone}</span>. Change
            the timezone in Business information.
          </p>

          <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
            {WEEKDAYS.map((weekday, index) => {
              const closed = days[index]?.is_closed ?? false;
              const error = errors.days?.[index]?.closes_at?.message;
              const errorId = `hours-${index}-error`;
              return (
                <li key={weekday} className="flex flex-col gap-3 p-4 lg:flex-row lg:items-start lg:gap-6">
                  <div className="flex items-center justify-between gap-3 lg:w-56 lg:shrink-0">
                    <p className="text-base font-bold">{weekday}</p>
                    <Switch
                      checked={closed}
                      pending={false}
                      label={`Closed: ${weekday}`}
                      onLabel="Closed"
                      offLabel="Open"
                      onToggle={() => toggleClosed(index)}
                    />
                  </div>

                  <div className="flex min-w-0 flex-1 flex-col gap-2">
                    {closed ? (
                      <p className="flex h-11 items-center text-sm text-muted-foreground">Closed all day</p>
                    ) : (
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <div className="flex flex-col gap-1.5">
                          <Label htmlFor={`hours-${index}-opens`}>Opens</Label>
                          <Input
                            id={`hours-${index}-opens`}
                            type="time"
                            aria-label={`${weekday} opens`}
                            aria-invalid={error ? true : undefined}
                            aria-describedby={error ? errorId : undefined}
                            {...register(`days.${index}.opens_at`)}
                          />
                        </div>
                        <div className="flex flex-col gap-1.5">
                          <Label htmlFor={`hours-${index}-closes`}>Closes</Label>
                          <Input
                            id={`hours-${index}-closes`}
                            type="time"
                            aria-label={`${weekday} closes`}
                            aria-invalid={error ? true : undefined}
                            aria-describedby={error ? errorId : undefined}
                            {...register(`days.${index}.closes_at`)}
                          />
                        </div>
                      </div>
                    )}
                    {error && (
                      <div id={errorId} className={cn(closed && "hidden")}>
                        <FieldError>{error}</FieldError>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>

          <div className="flex items-center gap-4">
            <Button type="submit" disabled={(configured && !isDirty) || isSubmitting}>
              {isSubmitting ? "Saving…" : "Save hours"}
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

/** Agent Builder section 6. Switches on the `GET /business/hours` result so a failed load never breaks the page. */
export function HoursEditor({ result }: { result: BusinessHoursResult }) {
  if (result.kind === "ok") return <HoursPanel initial={result.data} />;
  return (
    <SectionLoadFailure
      kind={result.kind}
      reason={result.kind === "unreachable" ? result.reason : undefined}
      noun="business hours"
    />
  );
}
