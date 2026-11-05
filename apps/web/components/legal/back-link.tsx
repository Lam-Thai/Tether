"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useSyncExternalStore } from "react";

import { buttonVariants } from "@/components/ui/button";
import { backTarget, readReturnPath } from "@/lib/navigation/return-path";

/** Re-read on `pageshow`: a page restored from the back-forward cache keeps its old render. */
function subscribe(onChange: () => void) {
  window.addEventListener("pageshow", onChange);
  return () => window.removeEventListener("pageshow", onChange);
}

function readStored(): string | null {
  try {
    return readReturnPath(window.sessionStorage);
  } catch {
    return null;
  }
}

/**
 * Back to the last page this tab showed before the legal page (landing, dashboard, sign-up,
 * ...), remembered by `<ReturnPathTracker />`. A tab with nothing remembered (the legal page
 * opened in a fresh tab, a bookmark in a new window, Google's OAuth review) goes home. A URL
 * typed into a tab that already visited the app still returns to that tab's last page, on
 * purpose: it is the page the visitor was on. The server snapshot is null, so the server and
 * first paint say "Back to home" and the client swaps in the remembered page without a
 * hydration mismatch.
 */
export function BackLink() {
  const stored = useSyncExternalStore(subscribe, readStored, () => null);
  const { href, label } = backTarget(stored);
  return (
    <Link href={href} className={buttonVariants({ variant: "outline", size: "sm" })}>
      <ArrowLeft aria-hidden />
      {label}
    </Link>
  );
}
