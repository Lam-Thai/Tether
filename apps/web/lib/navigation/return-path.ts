/**
 * Where the legal pages' Back link goes: the last real page this tab showed before the
 * visitor opened Privacy or Terms (landing, a dashboard page, sign-up, ...).
 *
 * `<ReturnPathTracker />` in the root layout remembers every returnable path in
 * sessionStorage (per tab, gone when the tab closes); the legal pages read it. Client-side
 * navigation never updates `document.referrer`, which is why this isn't referrer-based.
 * Opened directly (a bookmark, Google's OAuth review), Back goes home.
 */

export const RETURN_PATH_KEY = "tether:return-path";

type ReadStorage = Pick<Storage, "getItem">;
type WriteStorage = Pick<Storage, "setItem">;

/** Pages Back must never land on: the legal pages themselves, and one-shot callbacks. */
const NOT_RETURNABLE = ["/privacy", "/terms", "/sso-callback", "/dashboard/settings/calendar/callback", "/billing/return"];

/** A same-site path (no scheme, no `//host`, no backslash or control characters) worth returning to. */
export function isReturnablePath(path: string): boolean {
  if (!/^\/(?:[A-Za-z0-9?#][^\\]*)?$/.test(path)) return false;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(path)) return false;
  const pathname = path.split(/[?#]/, 1)[0] ?? "";
  return !NOT_RETURNABLE.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export function rememberPath(storage: WriteStorage, path: string): void {
  if (!isReturnablePath(path)) return;
  try {
    storage.setItem(RETURN_PATH_KEY, path);
  } catch {
    // Storage blocked or full: Back falls back to home.
  }
}

export function readReturnPath(storage: ReadStorage): string | null {
  try {
    const path = storage.getItem(RETURN_PATH_KEY);
    return path !== null && isReturnablePath(path) ? path : null;
  } catch {
    return null;
  }
}

const LABELS: [prefix: string, label: string][] = [
  ["/dashboard", "Back to dashboard"],
  ["/sign-up", "Back to sign-up"],
  ["/sign-in", "Back to sign-in"],
  ["/onboarding", "Back to setup"],
  ["/billing", "Back to billing"],
];

/** The Back link for a remembered path (or none): home when there is nothing to return to. */
export function backTarget(stored: string | null): { href: string; label: string } {
  if (stored === null) return { href: "/", label: "Back to home" };
  const pathname = stored.split(/[?#]/, 1)[0] ?? "";
  if (pathname === "/") return { href: stored, label: "Back to home" };
  const match = LABELS.find(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  return { href: stored, label: match ? match[1] : "Back" };
}
