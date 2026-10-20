/** "$29" for 2900 USD cents; drops ".00" so prices read like a price tag. */
export function formatPrice(amountMinor: number, currency: string): string {
  const amount = amountMinor / 100;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
    minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
  }).format(amount);
}

/** Whole days left until `iso`, rounded up, never negative. */
export function daysUntil(iso: string, now: Date = new Date()): number {
  const ms = new Date(iso).getTime() - now.getTime();
  return Math.max(0, Math.ceil(ms / 86_400_000));
}
