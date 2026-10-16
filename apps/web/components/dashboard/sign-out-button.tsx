"use client";

import { useClerk } from "@clerk/nextjs";

import { Button } from "@/components/ui/button";

export function SignOutButton() {
  const { signOut } = useClerk();

  return (
    <Button type="button" variant="outline" size="sm" onClick={() => signOut({ redirectUrl: "/" })}>
      Sign out
    </Button>
  );
}
