import { describe, expect, it } from "vitest";

import { formatDuration, formatPrice, priceToInput } from "./format-service";
import { serviceFormSchema } from "./validation/service";

describe("formatDuration", () => {
  it("formats minutes and hours", () => {
    expect(formatDuration(45)).toBe("45 min");
    expect(formatDuration(60)).toBe("1 h");
    expect(formatDuration(90)).toBe("1 h 30 min");
    expect(formatDuration(120)).toBe("2 h");
  });
});

describe("formatPrice", () => {
  it("formats in the business currency", () => {
    expect(formatPrice("45.00", "USD")).toBe("$45.00");
    expect(formatPrice("1234.5", "EUR")).toBe("€1,234.50");
  });

  it("uses zero decimals for JPY", () => {
    expect(formatPrice("5000", "JPY")).toBe("¥5,000");
    expect(formatPrice("5000.00", "JPY")).toBe("¥5,000");
  });

  it("says there is no listed price for null", () => {
    expect(formatPrice(null, "USD")).toBe("No listed price");
  });
});

describe("priceToInput", () => {
  it("shows the currency's own decimals", () => {
    expect(priceToInput("45.5", "USD")).toBe("45.50");
    expect(priceToInput("5000.00", "JPY")).toBe("5000");
    expect(priceToInput(null, "USD")).toBe("");
    // Never rounds a saved price the currency can't express.
    expect(priceToInput("45.50", "JPY")).toBe("45.50");
  });
});

describe("serviceFormSchema price", () => {
  const base = { name: "A", description: "", duration_minutes: "30", active: true };

  it("allows at most two decimals for USD and none for JPY", () => {
    const usd = serviceFormSchema("USD");
    expect(usd.safeParse({ ...base, price: "45.50" }).success).toBe(true);
    expect(usd.safeParse({ ...base, price: "45.555" }).success).toBe(false);
    expect(usd.safeParse({ ...base, price: "-1" }).success).toBe(false);
    expect(usd.safeParse({ ...base, price: "" }).success).toBe(true);
    const jpy = serviceFormSchema("JPY");
    expect(jpy.safeParse({ ...base, price: "5000" }).success).toBe(true);
    expect(jpy.safeParse({ ...base, price: "5000.5" }).success).toBe(false);
  });

  it("limits duration to 1-1440 whole minutes", () => {
    const schema = serviceFormSchema("USD");
    for (const bad of ["0", "1441", "1.5", "abc", ""]) {
      expect(schema.safeParse({ ...base, duration_minutes: bad, price: "" }).success).toBe(false);
    }
    expect(schema.safeParse({ ...base, duration_minutes: "1440", price: "" }).success).toBe(true);
  });
});
