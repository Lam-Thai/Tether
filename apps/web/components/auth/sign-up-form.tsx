"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
// See sign-in-form.tsx for why this imports from the `/legacy` subpath.
import { useSignUp } from "@clerk/nextjs/legacy";

import { extractClerkMessage } from "@/components/auth/clerk-errors";
import { signUpBlockedMessage, uncollectedFields } from "@/components/auth/clerk-status";
import { OAuthButtons, type OAuthStrategy } from "@/components/auth/oauth-buttons";
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

const SESSION_START_FAILED = (
  <>
    Your email is verified and your account is ready, but we couldn&apos;t sign you in.{" "}
    <Link href="/sign-in" className="underline">
      Sign in
    </Link>{" "}
    to continue.
  </>
);

export function SignUpForm() {
  const { isLoaded, signUp, setActive } = useSignUp();
  const router = useRouter();
  const [pendingVerification, setPendingVerification] = useState(false);
  const [formError, setFormError] = useState<React.ReactNode>(null);
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

      // A code can't finish a sign-up that also needs a field this form never
      // asks for (a Clerk dashboard setting), so don't send one.
      const blocking = uncollectedFields(created.missingFields);
      if (blocking.length > 0) {
        setFormError(signUpBlockedMessage(blocking, false));
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

  async function onOAuth(strategy: OAuthStrategy): Promise<boolean> {
    if (!isLoaded) {
      return false;
    }

    setFormError(null);
    try {
      await signUp.authenticateWithRedirect({
        strategy,
        redirectUrl: "/sso-callback",
        redirectUrlComplete: "/onboarding",
      });
      return true;
    } catch (error) {
      setFormError(extractClerkMessage(error, "Couldn't reach that sign-up provider. Please try again."));
      return false;
    }
  }

  async function onVerify(values: VerificationCodeValues) {
    if (!isLoaded) {
      return;
    }

    setFormError(null);
    setIsSubmitting(true);

    let sessionId: string;
    try {
      const result = await signUp.attemptEmailAddressVerification({ code: values.code });

      if (result.status === "complete" && result.createdSessionId) {
        sessionId = result.createdSessionId;
      } else {
        // Clerk accepted the code but wants a field this form doesn't collect:
        // say so instead of blaming the code.
        const blocking = uncollectedFields(result.missingFields);
        setFormError(
          blocking.length > 0
            ? signUpBlockedMessage(blocking, true)
            : "That code didn't work. Check your email and try again.",
        );
        setIsSubmitting(false);
        return;
      }
    } catch (error) {
      setFormError(extractClerkMessage(error, "That code didn't work. Check your email and try again."));
      setIsSubmitting(false);
      return;
    }

    // Only navigate once Clerk confirms the session is active — never show a
    // signed-up state before that's true. The account already exists here, so
    // a failure points to sign-in rather than to the code.
    try {
      await setActive({ session: sessionId });
      router.push("/onboarding");
    } catch {
      setFormError(SESSION_START_FAILED);
    } finally {
      setIsSubmitting(false);
    }
  }

  if (pendingVerification) {
    return (
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle as="h1">Check your email</CardTitle>
          <CardDescription>Enter the 6-digit code we just sent you.</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-4"
            onSubmit={verifyForm.handleSubmit(onVerify)}
            noValidate
          >
            {formError && <FormError>{formError}</FormError>}

            <div className="flex flex-col gap-2">
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
        <CardTitle as="h1">Create your account</CardTitle>
        <CardDescription>Set up Tether for your business.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {formError && <FormError>{formError}</FormError>}
        <OAuthButtons onSelect={onOAuth} disabled={isSubmitting} />
        <form className="flex flex-col gap-4" onSubmit={createForm.handleSubmit(onCreate)} noValidate>

          <div className="flex flex-col gap-2">
            <Label htmlFor="sign-up-email">Email</Label>
            <Input id="sign-up-email" type="email" autoComplete="email" {...createForm.register("email")} />
            {createForm.formState.errors.email && (
              <FieldError>{createForm.formState.errors.email.message}</FieldError>
            )}
          </div>

          <div className="flex flex-col gap-2">
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
