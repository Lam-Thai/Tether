import { isClerkAPIResponseError } from "@clerk/nextjs/errors";

/**
 * Clerk's custom-flow hooks throw `ClerkAPIResponseError` on a failed
 * `create`/`attempt*` call, with the user-facing message on `errors[0]`.
 * Any other thrown value (network failure, etc) falls back to a generic
 * message — this app never swallows the error entirely.
 */
export function extractClerkMessage(error: unknown, fallback: string): string {
  if (isClerkAPIResponseError(error)) {
    return error.errors[0]?.longMessage ?? error.errors[0]?.message ?? fallback;
  }
  return fallback;
}
