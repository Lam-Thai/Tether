import { currencyDigits } from "@/lib/validation/business";

/** 45 -> "45 min", 90 -> "1 h 30 min", 120 -> "2 h". */
export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/** Decimal-string price in the business currency, or the no-price label. Fixed locale: no hydration drift. */
export function formatPrice(price: string | null, currency: string): string {
  if (price === null) return "No listed price";
  const amount = Number(price);
  if (!Number.isFinite(amount)) return "No listed price";
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount);
  } catch {
    return `${amount.toFixed(currencyDigits(currency))} ${currency}`;
  }
}

/** Saved price -> the text shown in the price input (the currency's own number of decimals). */
export function priceToInput(price: string | null, currency: string): string {
  if (price === null) return "";
  const amount = Number(price);
  return Number.isFinite(amount) ? amount.toFixed(currencyDigits(currency)) : price;
}
