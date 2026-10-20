"use client";

import { useClerk } from "@clerk/nextjs";

import { Button, type ButtonProps } from "@/components/ui/button";

export function SignOutButton({ variant = "inverse" }: { variant?: ButtonProps["variant"] }) {
  const { signOut } = useClerk();

  return (
    <Button type="button" variant={variant} size="sm" onClick={() => signOut({ redirectUrl: "/" })}>
      Sign out
    </Button>
  );
}
