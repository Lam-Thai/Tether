"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuth } from "@clerk/nextjs";

import { createBusiness, type BusinessInput } from "@/lib/api/client";
import { businessInputSchema, TIMEZONE_OPTIONS, type BusinessFormValues } from "@/lib/validation/business";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

/**
 * Pre-fills the timezone select with the browser's own zone when it's a
 * recognized IANA name, so most owners don't have to search the list.
 */
function detectTimezone(): string {
  try {
    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return TIMEZONE_OPTIONS.includes(detected) ? detected : "";
  } catch {
    return "";
  }
}

export function OnboardingForm() {
  const { getToken } = useAuth();
  const router = useRouter();
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<BusinessFormValues>({
    resolver: zodResolver(businessInputSchema),
    defaultValues: {
      name: "",
      industry: "",
      address: "",
      timezone: detectTimezone(),
      phone: "",
      contact_email: "",
    },
  });

  async function onSubmit(values: BusinessFormValues) {
    setFormError(null);
    setIsSubmitting(true);

    try {
      const token = await getToken();
      if (!token) {
        setFormError("Your session has expired. Please sign in again.");
        return;
      }

      const input: BusinessInput = {
        name: values.name,
        industry: values.industry,
        address: values.address,
        timezone: values.timezone,
        phone: values.phone ? values.phone : null,
        contact_email: values.contact_email ? values.contact_email : null,
      };

      const result = await createBusiness(token, input);

      switch (result.kind) {
        case "ok":
          // Only navigate once the API confirms the business was created.
          router.push("/dashboard");
          return;
        case "already_exists":
          setFormError("This account already has a business set up.");
          return;
        case "invalid":
          setFormError(result.message);
          return;
        case "unauthorized":
          setFormError("Your session has expired. Please sign in again.");
          return;
        case "unreachable":
          setFormError(result.reason);
          return;
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Card className="w-full max-w-lg">
      <CardHeader>
        <CardTitle>Set up your business</CardTitle>
        <CardDescription>
          Tell Tether about your business so it can start answering as you.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={handleSubmit(onSubmit)} noValidate>
          {formError && <FormError>{formError}</FormError>}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="business-name">Business name</Label>
            <Input id="business-name" {...register("name")} />
            {errors.name && <FieldError>{errors.name.message}</FieldError>}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="business-industry">Industry</Label>
            <Input id="business-industry" {...register("industry")} />
            {errors.industry && <FieldError>{errors.industry.message}</FieldError>}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="business-address">Address</Label>
            <Input id="business-address" {...register("address")} />
            {errors.address && <FieldError>{errors.address.message}</FieldError>}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="business-timezone">Timezone</Label>
            <Select id="business-timezone" {...register("timezone")}>
              <option value="">Select a timezone…</option>
              {TIMEZONE_OPTIONS.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </Select>
            {errors.timezone && <FieldError>{errors.timezone.message}</FieldError>}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="business-phone">Phone (optional)</Label>
            <Input id="business-phone" type="tel" {...register("phone")} />
            {errors.phone && <FieldError>{errors.phone.message}</FieldError>}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="business-contact-email">Contact email (optional)</Label>
            <Input id="business-contact-email" type="email" {...register("contact_email")} />
            {errors.contact_email && <FieldError>{errors.contact_email.message}</FieldError>}
          </div>

          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Saving…" : "Create business"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
