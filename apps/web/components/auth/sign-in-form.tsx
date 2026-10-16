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
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { signInSchema, type SignInValues } from "@/lib/validation/auth";

export function SignInForm() {
  const { isLoaded, signIn, setActive } = useSignIn();
  const router = useRouter();
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

      if (result.status !== "complete" || !result.createdSessionId) {
        setFormError("Sign-in could not be completed. Please try again.");
        return;
      }

      // Only navigate once Clerk confirms the session was created — never
      // show a signed-in state before that's true.
      await setActive({ session: result.createdSessionId });
      router.push("/dashboard");
    } catch (error) {
      setFormError(extractClerkMessage(error, "Something went wrong signing in. Please try again."));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
        <CardDescription>Access your Tether workspace.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={handleSubmit(onSubmit)} noValidate>
          {formError && <FormError>{formError}</FormError>}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sign-in-email">Email</Label>
            <Input id="sign-in-email" type="email" autoComplete="email" {...register("email")} />
            {errors.email && <FieldError>{errors.email.message}</FieldError>}
          </div>

          <div className="flex flex-col gap-1.5">
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
