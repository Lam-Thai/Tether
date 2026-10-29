import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PrivacyPage, { metadata as privacyMetadata } from "@/app/privacy/page";
import TermsPage, { metadata as termsMetadata } from "@/app/terms/page";
import { LegalFact } from "@/components/legal/legal-fact";
import { LegalLinks } from "@/components/legal/legal-links";
import { PRIVACY_SECTIONS } from "@/components/legal/privacy-sections";
import { TERMS_SECTIONS } from "@/components/legal/terms-sections";
import { CONTACT_EMAIL, formatLegalDate, LAST_UPDATED, LEGAL_NAME } from "@/lib/legal";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// Google's OAuth verification requires this sentence word for word.
const LIMITED_USE =
  "Tether's use and transfer of information received from Google APIs to any other app will adhere to the Google API Services User Data Policy, including the Limited Use requirements.";

describe("privacy policy", () => {
  it("has metadata and shows the last updated date", () => {
    expect(privacyMetadata.title).toBe("Privacy Policy");
    expect(privacyMetadata.description).toEqual(expect.any(String));

    render(<PrivacyPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Privacy Policy" })).toBeInTheDocument();
    const date = screen.getByText(formatLegalDate(LAST_UPDATED));
    expect(date.tagName).toBe("TIME");
    expect(date).toHaveAttribute("dateTime", LAST_UPDATED);
  });

  it("includes the exact Google Limited Use statement, linked to the policy", () => {
    render(<PrivacyPage />);
    const statement = screen.getByText((_, element) =>
      element?.tagName === "P" && element.textContent === LIMITED_USE,
    );
    expect(
      within(statement).getByRole("link", { name: "Google API Services User Data Policy" }),
    ).toHaveAttribute("href", "https://developers.google.com/terms/api-services-user-data-policy");
  });

  it("names the three Calendar scopes, each with its purpose", () => {
    render(<PrivacyPage />);
    const scopes = {
      "calendar.calendarlist.readonly": /list the calendars you own/i,
      "calendar.freebusy": /free\/busy/i,
      "calendar.events.owned": /create and cancel only the bookings Tether makes/i,
    };
    for (const [scope, purpose] of Object.entries(scopes)) {
      const item = screen.getByText(scope).closest("li");
      expect(item, scope).not.toBeNull();
      expect(item).toHaveTextContent(purpose);
    }
  });

  it("says Google user data is never sold, used for ads, or used to train AI models", () => {
    render(<PrivacyPage />);
    const section = document.getElementById("google-user-data");
    expect(section).not.toBeNull();
    expect(section).toHaveTextContent(/is not sold, and is not used for advertising/);
    expect(section).toHaveTextContent(/is not read by people, except with your permission, when needed for security, or when the law requires it/);
    expect(section).toHaveTextContent(/is not used by Tether to develop, improve or train generalized AI or machine learning models/);
    // The free-tier caveat must stay next to the promise until Tether is on a paid Gemini tier.
    expect(section).toHaveTextContent(/free tier of the Gemini API/);
    expect(within(section!).getByRole("link", { name: /myaccount\.google\.com\/permissions/ })).toHaveAttribute(
      "href",
      "https://myaccount.google.com/permissions",
    );
  });
});

describe("terms of service", () => {
  it("has metadata and shows the last updated date", () => {
    expect(termsMetadata.title).toBe("Terms of Service");
    expect(termsMetadata.description).toEqual(expect.any(String));

    render(<TermsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Terms of Service" })).toBeInTheDocument();
    expect(screen.getByText(formatLegalDate(LAST_UPDATED))).toHaveAttribute("dateTime", LAST_UPDATED);
  });
});

describe.each([
  ["privacy", PrivacyPage, PRIVACY_SECTIONS],
  ["terms", TermsPage, TERMS_SECTIONS],
])("%s layout", (_, Page, sections) => {
  it("links every section from the table of contents to a heading with that id", () => {
    render(<Page />);
    const toc = screen.getByRole("navigation", { name: "On this page" });
    const links = within(toc).getAllByRole("link");
    expect(links).toHaveLength(sections.length);

    for (const [i, section] of sections.entries()) {
      expect(links[i]).toHaveAttribute("href", `#${section.id}`);
      const heading = screen.getByRole("heading", { level: 2, name: section.title });
      expect(heading.closest("section")).toHaveAttribute("id", section.id);
    }
  });

  it("has unique section ids", () => {
    expect(new Set(sections.map((s) => s.id)).size).toBe(sections.length);
  });

  it("links to the other legal page and home", () => {
    render(<Page />);
    expect(screen.getByRole("link", { name: "Tether home" })).toHaveAttribute("href", "/");
    const legal = screen.getByRole("navigation", { name: "Legal" });
    expect(within(legal).getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "/privacy");
    expect(within(legal).getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/terms");
  });
});

describe.each([
  ["app/privacy/page.tsx", PrivacyPage],
  ["app/terms/page.tsx", TermsPage],
])("%s", (file, Page) => {
  it("states the legal facts from lib/legal.ts, or marks them as still to be confirmed", () => {
    render(<Page />);
    expect(screen.getAllByText(LEGAL_NAME ?? "[legal name: to be confirmed]").length).toBeGreaterThan(0);
    expect(screen.getAllByText(CONTACT_EMAIL ?? "[contact email: to be confirmed]").length).toBeGreaterThan(0);
  });

  it("stays static and never reads the session, so it renders the same signed out", () => {
    const source = readFileSync(path.join(webRoot, file), "utf8");
    for (const banned of ["auth(", "cookies(", "headers(", "currentUser(", "export const dynamic"]) {
      expect(source, banned).not.toContain(banned);
    }
  });
});

describe("LegalFact", () => {
  it("renders a value the owner filled in", () => {
    render(<LegalFact value="Example Co" label="legal name" />);
    expect(screen.getByText("Example Co")).toBeInTheDocument();
  });

  it("marks a missing value visibly instead of inventing one", () => {
    render(<LegalFact value={null} label="legal name" />);
    expect(screen.getByText("[legal name: to be confirmed]")).toBeInTheDocument();
  });

  it("links an email address", () => {
    render(<LegalFact value="hello@example.com" label="contact email" email />);
    expect(screen.getByRole("link", { name: "hello@example.com" })).toHaveAttribute(
      "href",
      "mailto:hello@example.com",
    );
  });
});

describe("LegalLinks", () => {
  it("links Privacy and Terms and marks the current page", () => {
    render(<LegalLinks current="/terms" />);
    expect(screen.getByRole("link", { name: "Privacy" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Terms" })).toHaveAttribute("aria-current", "page");
  });
});

describe("formatLegalDate", () => {
  it("formats an ISO date in a fixed locale, with no timezone shift", () => {
    expect(formatLegalDate("2026-01-01")).toBe("January 1, 2026");
  });
});
