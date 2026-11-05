"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useSyncExternalStore } from "react";

import { buttonVariants } from "@/components/ui/button";
import { backTarget, readReturnPath } from "@/lib/navigation/return-path";

const noSubscribe = () => () => {};

function readStored(): string | null {
  try {
    return readReturnPath(window.sessionStorage);
  } catch {
    return null;
  }
}

/**
 * Back to the page the visitor came from (landing, dashboard, sign-up, ...), remembered by
 * `<ReturnPathTracker />`; home when they opened the legal page directly. The server
 * snapshot is null, so the server and first paint say "Back to home" and the client swaps
 * in the remembered page without a hydration mismatch.
 */
export function BackLink() {
  const stored = useSyncExternalStore(noSubscribe, readStored, () => null);
  const { href, label } = backTarget(stored);
  return (
    <Link href={href} className={buttonVariants({ variant: "outline", size: "sm" })}>
      <ArrowLeft aria-hidden />
      {label}
    </Link>
  );
}
