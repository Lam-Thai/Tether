"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
// The main `@clerk/nextjs` entry's `useSignIn` returns a newer
// signal-based shape (`{ signIn, errors, fetchStatus }`). This app builds
// its own form chrome around the classic `{ isLoaded, signIn, setActive }`
// API instead, which still lives at the `/legacy` subpath.
import { useSignIn } from "@clerk/nextjs/legacy";

import { extractClerkMessage } from "@/components/auth/clerk-errors";
import { emailCodeFactor } from "@/components/auth/clerk-status";
import { OAuthButtons, type OAuthStrategy } from "@/components/auth/oauth-buttons";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  signInSchema,
  verificationCodeSchema,
  type SignInValues,
  type VerificationCodeValues,
} from "@/lib/validation/auth";

const SESSION_START_FAILED = "Your sign-in was accepted, but we couldn't start your session. Please try again.";

/** Why Clerk wants an emailed code: a new device (client trust) or the account's second factor. */
type CodeStep = { reason: "new_device" | "second_factor"; sentTo: string | null };

export function SignInForm() {
  const { isLoaded, signIn, setActive } = useSignIn();
  const router = useRouter();
  const [codeStep, setCodeStep] = useState<CodeStep | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<SignInValues>({
    resolver: zodResolver(signInSchema),
    defaultValues: { email: "", password: "" },
  });

  const verifyForm = useForm<VerificationCodeValues>({
    resolver: zodResolver(verificationCodeSchema),
    defaultValues: { code: "" },
  });

  /** Only navigate once Clerk confirms the session is active — never show a signed-in state before that. */
  async function activate(sessionId: string) {
    if (!setActive) {
      setFormError(SESSION_START_FAILED);
      return;
    }
    try {
      await setActive({ session: sessionId });
      router.push("/dashboard");
    } catch {
      setFormError(SESSION_START_FAILED);
    }
  }

  async function onSubmit(values: SignInValues) {
    if (!isLoaded) {
      return;
    }

    setFormError(null);
    setIsSubmitting(true);

    try {
      const result = await signIn.create({
        identifier: values.email,
        password: values.password,
      });

      if (result.status === "complete" && result.createdSessionId) {
        await activate(result.createdSessionId);
        return;
      }

      if (result.status === "needs_client_trust" || result.status === "needs_second_factor") {
        const factor = emailCodeFactor(result.supportedSecondFactors);
        if (!factor) {
          setFormError(
            "Your account needs a second verification step this form doesn't support yet, such as an authenticator app. Please contact support.",
          );
          return;
        }
        await signIn.prepareSecondFactor({
          strategy: "email_code",
          emailAddressId: factor.emailAddressId,
        });
        setCodeStep({
          reason: result.status === "needs_client_trust" ? "new_device" : "second_factor",
          sentTo: factor.safeIdentifier ?? null,
        });
        return;
      }

      if (result.status === "needs_new_password") {
        setFormError("Your password needs to be reset before you can sign in. Please contact support.");
        return;
      }

      setFormError("Sign-in needs a step this form doesn't support yet. Please try again or contact support.");
    } catch (error) {
      setFormError(extractClerkMessage(error, "Something went wrong signing in. Please try again."));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function onOAuth(strategy: OAuthStrategy): Promise<boolean> {
    if (!isLoaded) {
      return false;
    }

    setFormError(null);
    try {
      await signIn.authenticateWithRedirect({
        strategy,
        redirectUrl: "/sso-callback",
        redirectUrlComplete: "/dashboard",
      });
      return true;
    } catch (error) {
      setFormError(extractClerkMessage(error, "Couldn't reach that sign-in provider. Please try again."));
      return false;
    }
  }

  async function onVerify(values: VerificationCodeValues) {
    if (!isLoaded) {
      return;
    }

    setFormError(null);
    setIsSubmitting(true);

    try {
      const result = await signIn.attemptSecondFactor({ strategy: "email_code", code: values.code });

      if (result.status !== "complete" || !result.createdSessionId) {
        setFormError("That code didn't work. Check your email and try again.");
        return;
      }

      await activate(result.createdSessionId);
    } catch (error) {
      setFormError(extractClerkMessage(error, "That code didn't work. Check your email and try again."));
    } finally {
      setIsSubmitting(false);
    }
  }

  if (codeStep) {
    const to = codeStep.sentTo ?? "your email";
    return (
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle as="h1">Check your email</CardTitle>
          <CardDescription>
            {codeStep.reason === "new_device"
              ? `You're signing in from a new device. Enter the 6-digit code we sent to ${to}.`
              : `Enter the 6-digit code we sent to ${to}.`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-4"
            onSubmit={verifyForm.handleSubmit(onVerify)}
            noValidate
          >
            {formError && <FormError>{formError}</FormError>}

            <div className="flex flex-col gap-2">
              <Label htmlFor="sign-in-code">Verification code</Label>
              <Input
                id="sign-in-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                {...verifyForm.register("code")}
              />
              {verifyForm.formState.errors.code && (
                <FieldError>{verifyForm.formState.errors.code.message}</FieldError>
              )}
            </div>

            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Verifying…" : "Verify and sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle as="h1">Sign in</CardTitle>
        <CardDescription>Access your Tether workspace.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {formError && <FormError>{formError}</FormError>}
        <OAuthButtons onSelect={onOAuth} disabled={isSubmitting} />
        <form className="flex flex-col gap-4" onSubmit={handleSubmit(onSubmit)} noValidate>

          <div className="flex flex-col gap-2">
            <Label htmlFor="sign-in-email">Email</Label>
            <Input id="sign-in-email" type="email" autoComplete="email" {...register("email")} />
            {errors.email && <FieldError>{errors.email.message}</FieldError>}
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="sign-in-password">Password</Label>
            <Input
              id="sign-in-password"
              type="password"
              autoComplete="current-password"
              {...register("password")}
            />
            {errors.password && <FieldError>{errors.password.message}</FieldError>}
          </div>

          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
