"use client";

import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuth } from "@clerk/nextjs";
import { CircleCheck } from "lucide-react";

import { putBookingRules, type BookingRulesRead, type BookingRulesResult } from "@/lib/api/client";
import {
  SLOT_INTERVALS,
  bookingRulesFormSchema,
  fromApiRules,
  toApiRules,
  type BookingRulesFormValues,
} from "@/lib/validation/booking-rules";
import { SESSION_EXPIRED, SectionLoadFailure, commonFailure } from "@/components/agent/section-shared";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

type SaveState = { kind: "idle" } | { kind: "saved" } | { kind: "error"; message: ReactNode };

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

function BookingRulesPanel({ initial }: { initial: BookingRulesRead }) {
  const { getToken } = useAuth();
  const [customized, setCustomized] = useState(initial.customized);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<BookingRulesFormValues>({
    resolver: zodResolver(bookingRulesFormSchema),
    defaultValues: fromApiRules(initial),
  });

  async function onSubmit(values: BookingRulesFormValues) {
    setSaveState({ kind: "idle" });

    const token = await getToken();
    if (!token) {
      setSaveState({ kind: "error", message: SESSION_EXPIRED });
      return;
    }

    const result = await putBookingRules(token, toApiRules(values));

    if (result.kind === "ok") {
      // Only now are they saved: adopt what the API returned as the new baseline.
      setCustomized(result.data.customized);
      reset(fromApiRules(result.data));
      setSaveState({ kind: "saved" });
    } else if (result.kind === "validation") {
      setSaveState({ kind: "error", message: result.message });
    } else if (result.kind === "unreachable") {
      setSaveState({ kind: "error", message: result.reason });
    } else {
      setSaveState({ kind: "error", message: commonFailure(result.kind) });
    }
  }

  // The API accepts any multiple of 5 (5..240): keep a saved value that is not a preset selectable.
  const intervalOptions = [...new Set<number>([...SLOT_INTERVALS, initial.slot_interval_minutes])].sort((a, b) => a - b);

  const describe = (id: string, error?: string) => (error ? `${id}-hint ${id}-error` : `${id}-hint`);

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Booking behavior</CardTitle>
        <CardDescription>
          How the receptionist offers appointment times from your connected calendar.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-5"
          onSubmit={handleSubmit(onSubmit)}
          onChange={() => {
            if (saveState.kind !== "idle") setSaveState({ kind: "idle" });
          }}
          noValidate
        >
          {!customized && (
            <Callout tone="warning" compact headingLevel={3} title="Using defaults">
              You have not saved any booking rules, so the receptionist uses the standard ones shown
              below. They apply until you press Save booking rules.
            </Callout>
          )}
          {saveState.kind === "error" && <FormError>{saveState.message}</FormError>}

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <Field
              id="booking-interval"
              label="Appointment start times every"
              hint="Offered times fall on this grid, such as 9:00, 9:30."
              error={errors.slot_interval_minutes?.message}
            >
              <Select
                id="booking-interval"
                aria-invalid={errors.slot_interval_minutes ? true : undefined}
                aria-describedby={describe("booking-interval", errors.slot_interval_minutes?.message)}
                {...register("slot_interval_minutes")}
              >
                {intervalOptions.map((minutes) => (
                  <option key={minutes} value={String(minutes)}>
                    {minutes} minutes
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="booking-notice"
              label="Minimum notice"
              hint="The earliest a customer can book from now."
              error={errors.min_notice_value?.message}
            >
              <div className="grid grid-cols-[1fr_auto] gap-2">
                <Input
                  id="booking-notice"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  step={1}
                  aria-invalid={errors.min_notice_value ? true : undefined}
                  aria-describedby={describe("booking-notice", errors.min_notice_value?.message)}
                  {...register("min_notice_value")}
                />
                <Select aria-label="Minimum notice unit" {...register("min_notice_unit")}>
                  <option value="minutes">minutes</option>
                  <option value="hours">hours</option>
                </Select>
              </div>
            </Field>

            <Field
              id="booking-days"
              label="Book up to this many days ahead"
              hint="Between 1 and 365 days."
              error={errors.max_days_ahead?.message}
            >
              <Input
                id="booking-days"
                type="number"
                inputMode="numeric"
                min={1}
                max={365}
                step={1}
                aria-invalid={errors.max_days_ahead ? true : undefined}
                aria-describedby={describe("booking-days", errors.max_days_ahead?.message)}
                {...register("max_days_ahead")}
              />
            </Field>

            <Field
              id="booking-buffer"
              label="Buffer between appointments (minutes)"
              hint="Kept free before and after every busy time. Multiples of 5, up to 240."
              error={errors.buffer_minutes?.message}
            >
              <Input
                id="booking-buffer"
                type="number"
                inputMode="numeric"
                min={0}
                max={240}
                step={5}
                aria-invalid={errors.buffer_minutes ? true : undefined}
                aria-describedby={describe("booking-buffer", errors.buffer_minutes?.message)}
                {...register("buffer_minutes")}
              />
            </Field>
          </div>

          <div className="flex items-center gap-4">
            <Button type="submit" disabled={(customized && !isDirty) || isSubmitting}>
              {isSubmitting ? "Saving…" : "Save booking rules"}
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

/** Agent Builder "Booking behavior". Switches on the `GET /business/booking-rules` result so a failed load never breaks the page. */
export function BookingRulesEditor({ result }: { result: BookingRulesResult }) {
  if (result.kind === "ok") return <BookingRulesPanel initial={result.data} />;
  return (
    <SectionLoadFailure
      kind={result.kind}
      reason={result.kind === "unreachable" ? result.reason : undefined}
      noun="booking rules"
    />
  );
}
