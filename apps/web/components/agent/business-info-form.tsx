"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuth } from "@clerk/nextjs";
import { CircleCheck } from "lucide-react";

import {
  updateBusiness,
  type BusinessInput,
  type BusinessRead,
  type BusinessResult,
} from "@/lib/api/client";
import {
  CURRENCY_CODES,
  TIMEZONE_OPTIONS,
  businessInfoSchema,
  type BusinessInfoValues,
} from "@/lib/validation/business";
import { SESSION_EXPIRED, SectionLoadFailure } from "@/components/agent/section-shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

function toFormValues(business: BusinessRead): BusinessInfoValues {
  return {
    name: business.name,
    industry: business.industry,
    address: business.address,
    timezone: business.timezone,
    phone: business.phone ?? "",
    contact_email: business.contact_email ?? "",
    currency: business.currency,
  };
}

/** Only the fields whose validated value differs from what was last saved; blank phone / email send null. */
export function changedFields(values: BusinessInfoValues, saved: BusinessRead): Partial<BusinessInput> {
  const patch: Partial<BusinessInput> = {};
  if (values.name !== saved.name) patch.name = values.name;
  if (values.industry !== saved.industry) patch.industry = values.industry;
  if (values.address !== saved.address) patch.address = values.address;
  if (values.timezone !== saved.timezone) patch.timezone = values.timezone;
  if (values.phone !== (saved.phone ?? "")) patch.phone = values.phone || null;
  if (values.contact_email !== (saved.contact_email ?? "")) patch.contact_email = values.contact_email || null;
  if (values.currency !== saved.currency) patch.currency = values.currency;
  return patch;
}

/** The list plus the current value, so a saved zone/currency missing from the runtime list still shows. */
function withCurrent(options: readonly string[], current: string): string[] {
  return options.includes(current) ? [...options] : [current, ...options];
}

type SaveState = { kind: "idle" } | { kind: "saved" } | { kind: "error"; message: string };

function Field({
  id,
  label,
  error,
  help,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  help?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {help && !error && (
        <p id={`${id}-help`} className="text-sm text-muted-foreground">
          {help}
        </p>
      )}
      {error && (
        <div id={`${id}-error`}>
          <FieldError>{error}</FieldError>
        </div>
      )}
    </div>
  );
}

function BusinessInfoPanel({ business }: { business: BusinessRead }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [saved, setSaved] = useState<BusinessRead>(business);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<BusinessInfoValues>({
    resolver: zodResolver(businessInfoSchema),
    defaultValues: toFormValues(business),
  });

  const describedBy = (id: string, hasError: boolean, hasHelp = false) =>
    hasError ? `${id}-error` : hasHelp ? `${id}-help` : undefined;

  async function onSubmit(values: BusinessInfoValues) {
    setSaveState({ kind: "idle" });

    const patch = changedFields(values, saved);
    if (Object.keys(patch).length === 0) return;

    const token = await getToken();
    if (!token) {
      setSaveState({ kind: "error", message: SESSION_EXPIRED });
      return;
    }

    const result = await updateBusiness(token, saved.id, patch);

    switch (result.kind) {
      case "ok":
        // Only now is it saved: adopt what the API returned as the new baseline.
        setSaved(result.data);
        reset(toFormValues(result.data));
        setSaveState({ kind: "saved" });
        router.refresh();
        return;
      case "invalid":
        setSaveState({ kind: "error", message: result.message });
        return;
      case "unauthorized":
        setSaveState({ kind: "error", message: SESSION_EXPIRED });
        return;
      case "not_found":
        setSaveState({ kind: "error", message: "This business no longer exists. Reload the page." });
        return;
      case "unreachable":
        setSaveState({ kind: "error", message: result.reason });
        return;
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Business information</CardTitle>
        <CardDescription>
          The receptionist tells customers this: your name, address, phone and email. Keep it
          accurate, then check it in the test chat.
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
          {saveState.kind === "error" && <FormError>{saveState.message}</FormError>}

          <Field id="biz-name" label="Business name" error={errors.name?.message}>
            <Input
              id="biz-name"
              autoComplete="off"
              aria-invalid={errors.name ? true : undefined}
              aria-describedby={describedBy("biz-name", !!errors.name)}
              {...register("name")}
            />
          </Field>

          <Field id="biz-industry" label="Industry" error={errors.industry?.message}>
            <Input
              id="biz-industry"
              autoComplete="off"
              aria-invalid={errors.industry ? true : undefined}
              aria-describedby={describedBy("biz-industry", !!errors.industry)}
              {...register("industry")}
            />
          </Field>

          <Field id="biz-address" label="Address" error={errors.address?.message}>
            <Input
              id="biz-address"
              autoComplete="off"
              aria-invalid={errors.address ? true : undefined}
              aria-describedby={describedBy("biz-address", !!errors.address)}
              {...register("address")}
            />
          </Field>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <Field
              id="biz-phone"
              label="Phone (optional)"
              error={errors.phone?.message}
              help="Leave blank to remove it."
            >
              <Input
                id="biz-phone"
                type="tel"
                autoComplete="off"
                aria-invalid={errors.phone ? true : undefined}
                aria-describedby={describedBy("biz-phone", !!errors.phone, true)}
                {...register("phone")}
              />
            </Field>

            <Field
              id="biz-email"
              label="Contact email (optional)"
              error={errors.contact_email?.message}
              help="Leave blank to remove it."
            >
              <Input
                id="biz-email"
                type="email"
                autoComplete="off"
                aria-invalid={errors.contact_email ? true : undefined}
                aria-describedby={describedBy("biz-email", !!errors.contact_email, true)}
                {...register("contact_email")}
              />
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <Field id="biz-timezone" label="Timezone" error={errors.timezone?.message}>
              <Select
                id="biz-timezone"
                aria-invalid={errors.timezone ? true : undefined}
                aria-describedby={describedBy("biz-timezone", !!errors.timezone)}
                {...register("timezone")}
              >
                {withCurrent(TIMEZONE_OPTIONS, saved.timezone).map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="biz-currency"
              label="Currency"
              error={errors.currency?.message}
              help="Service prices are shown in this currency. Changing it does not convert them."
            >
              <Select
                id="biz-currency"
                aria-invalid={errors.currency ? true : undefined}
                aria-describedby={describedBy("biz-currency", !!errors.currency, true)}
                {...register("currency")}
              >
                {withCurrent(CURRENCY_CODES, saved.currency).map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

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

/**
 * Agent Builder section 3. Switches on the `GET /business` result so a failed
 * load shows a Callout here without breaking the rest of the page.
 */
export function BusinessInfoForm({ result }: { result: BusinessResult }) {
  if (result.kind === "ok") return <BusinessInfoPanel business={result.data} />;
  return (
    <SectionLoadFailure
      kind={result.kind}
      reason={result.kind === "unreachable" ? result.reason : undefined}
      noun="business information"
    />
  );
}
