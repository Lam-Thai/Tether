import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ClosingCta, LandingFooter } from "@/components/landing/closing-footer";
import { Hero } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { LandingHeader } from "@/components/landing/landing-header";
import { Pricing } from "@/components/landing/pricing";
import { PrimaryCta } from "@/components/landing/primary-cta";

describe("PrimaryCta", () => {
  it("links signed-out visitors to sign-up", () => {
    render(<PrimaryCta signedIn={false} />);
    expect(screen.getByRole("link", { name: "Start free trial" })).toHaveAttribute(
      "href",
      "/sign-up",
    );
  });

  it("links signed-in visitors to the dashboard", () => {
    render(<PrimaryCta signedIn />);
    expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(screen.queryByText("Start free trial")).not.toBeInTheDocument();
  });
});

describe("Pricing", () => {
  it("shows the price, trial and no-card terms", () => {
    render(<Pricing signedIn={false} />);
    expect(screen.getByText("$29")).toBeInTheDocument();
    expect(screen.getByText("14-day free trial")).toBeInTheDocument();
    expect(screen.getByText("No card required")).toBeInTheDocument();
  });
});

describe("landing sections", () => {
  it("render without crashing", () => {
    render(
      <>
        <LandingHeader signedIn={false} />
        <Hero signedIn={false} />
        <HowItWorks />
        <ClosingCta signedIn={false} />
        <LandingFooter />
      </>,
    );
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "System status" })).toHaveAttribute("href", "/status");
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });
});
