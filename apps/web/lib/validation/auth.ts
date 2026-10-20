import { z } from "zod";

export const signInSchema = z.object({
  email: z.string().trim().min(1, "Email is required.").email("Enter a valid email address."),
  password: z.string().min(1, "Password is required."),
});

export type SignInValues = z.infer<typeof signInSchema>;

export const signUpSchema = z.object({
  email: z.string().trim().min(1, "Email is required.").email("Enter a valid email address."),
  password: z.string().min(8, "Use at least 8 characters."),
});

export type SignUpValues = z.infer<typeof signUpSchema>;

export const verificationCodeSchema = z.object({
  code: z
    .string()
    .trim()
    .min(6, "Enter the 6-digit code from your email.")
    .max(6, "Enter the 6-digit code from your email."),
});

export type VerificationCodeValues = z.infer<typeof verificationCodeSchema>;
