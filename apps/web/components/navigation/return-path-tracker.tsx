"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { rememberPath } from "@/lib/navigation/return-path";

/**
 * Remembers the current page (path + query) for the legal pages' Back link. Mounted once in
 * the root layout inside a Suspense boundary (it reads search params). Renders nothing.
 */
export function ReturnPathTracker() {
  const pathname = usePathname();
  const search = useSearchParams().toString();

  useEffect(() => {
    try {
      rememberPath(window.sessionStorage, search ? `${pathname}?${search}` : pathname);
    } catch {
      // `window.sessionStorage` itself throws when site data is blocked.
    }
  }, [pathname, search]);

  return null;
}
