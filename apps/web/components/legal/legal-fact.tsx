import { INLINE_LINK } from "@/components/legal/prose";

/**
 * A fact from `lib/legal.ts` inside legal copy. A value the owner hasn't set
 * yet shows as a bracketed "to be confirmed" mark, never as a made-up value.
 */
export function LegalFact({
  value,
  label,
  email = false,
}: {
  value: string | null;
  /** Names the missing fact, e.g. "contact email". */
  label: string;
  /** Render the value as a mailto link. */
  email?: boolean;
}) {
  if (value === null) {
    return <mark className="rounded-sm bg-accent px-1 text-accent-foreground">[{label}: to be confirmed]</mark>;
  }
  if (email) {
    return (
      <a href={`mailto:${value}`} className={INLINE_LINK}>
        {value}
      </a>
    );
  }
  return <>{value}</>;
}
