"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
// See sign-in-form.tsx for why this imports from the `/legacy` subpath.
import { useSignUp } from "@clerk/nextjs/legacy";

import { extractClerkMessage } from "@/components/auth/clerk-errors";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  signUpSchema,
  verificationCodeSchema,
  type SignUpValues,
  type VerificationCodeValues,
} from "@/lib/validation/auth";

export function SignUpForm() {
  const { isLoaded, signUp, setActive } = useSignUp();
  const router = useRouter();
  const [pendingVerification, setPendingVerification] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const createForm = useForm<SignUpValues>({
    resolver: zodResolver(signUpSchema),
    defaultValues: { email: "", password: "" },
  });

  const verifyForm = useForm<VerificationCodeValues>({
    resolver: zodResolver(verificationCodeSchema),
    defaultValues: { code: "" },
  });

  async function onCreate(values: SignUpValues) {
    if (!isLoaded) {
      return;
    }

    setFormError(null);
    setIsSubmitting(true);

    try {
      const created = await signUp.create({
        emailAddress: values.email,
        password: values.password,
      });

      // Some Clerk instances skip email verification entirely — handle
      // that "already verified" path instead of forcing a code step that
      // will never be needed.
      if (created.status === "complete" && created.createdSessionId) {
        await setActive({ session: created.createdSessionId });
        router.push("/onboarding");
        return;
      }

      await signUp.prepareEmailAddressVerification({ strategy: "email_code" });
      setPendingVerification(true);
    } catch (error) {
      setFormError(
        extractClerkMessage(error, "Something went wrong creating your account. Please try again."),
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  async function onVerify(values: VerificationCodeValues) {
    if (!isLoaded) {
      return;
    }

    setFormError(null);
    setIsSubmitting(true);

    try {
      const result = await signUp.attemptEmailAddressVerification({ code: values.code });

      if (result.status !== "complete" || !result.createdSessionId) {
        setFormError("That code didn't work. Check your email and try again.");
        return;
      }

      // Only navigate once Clerk confirms the session was created — never
      // show a signed-up state before that's true.
      await setActive({ session: result.createdSessionId });
      router.push("/onboarding");
    } catch (error) {
      setFormError(extractClerkMessage(error, "That code didn't work. Check your email and try again."));
    } finally {
      setIsSubmitting(false);
    }
  }

  if (pendingVerification) {
    return (
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Check your email</CardTitle>
          <CardDescription>Enter the 6-digit code we just sent you.</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-4"
            onSubmit={verifyForm.handleSubmit(onVerify)}
            noValidate
          >
            {formError && <FormError>{formError}</FormError>}

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sign-up-code">Verification code</Label>
              <Input
                id="sign-up-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                {...verifyForm.register("code")}
              />
              {verifyForm.formState.errors.code && (
                <FieldError>{verifyForm.formState.errors.code.message}</FieldError>
              )}
            </div>

            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Verifying…" : "Verify email"}
            </Button>
          </form>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Create your account</CardTitle>
        <CardDescription>Set up Tether for your business.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={createForm.handleSubmit(onCreate)} noValidate>
          {formError && <FormError>{formError}</FormError>}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sign-up-email">Email</Label>
            <Input id="sign-up-email" type="email" autoComplete="email" {...createForm.register("email")} />
            {createForm.formState.errors.email && (
              <FieldError>{createForm.formState.errors.email.message}</FieldError>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sign-up-password">Password</Label>
            <Input
              id="sign-up-password"
              type="password"
              autoComplete="new-password"
              {...createForm.register("password")}
            />
            {createForm.formState.errors.password && (
              <FieldError>{createForm.formState.errors.password.message}</FieldError>
            )}
          </div>

          {/* Clerk's bot-protection (Smart CAPTCHA) widget mounts here when enabled. */}
          <div id="clerk-captcha" />

          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Creating account…" : "Create account"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
