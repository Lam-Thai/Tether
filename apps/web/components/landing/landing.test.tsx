import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClosingCta, LandingFooter } from "@/components/landing/closing-footer";
import { Hero } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { Journey } from "@/components/landing/journey";
import { LandingHeader } from "@/components/landing/landing-header";
import { ScrollReveal } from "@/components/landing/motion/scroll-reveal";
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

describe("legal links", () => {
  it.each([
    ["signed out", false],
    ["signed in", true],
  ])("the header links Privacy and Terms (%s)", (_, signedIn) => {
    render(<LandingHeader signedIn={signedIn} />);
    const legal = screen.getByRole("navigation", { name: "Legal" });
    expect(within(legal).getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "/privacy");
    expect(within(legal).getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/terms");
  });

  it("the footer links Privacy and Terms without a second Legal landmark", () => {
    render(<LandingFooter />);
    const footer = screen.getByRole("contentinfo");
    expect(within(footer).getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "/privacy");
    expect(within(footer).getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/terms");
    expect(screen.queryByRole("navigation", { name: "Legal" })).not.toBeInTheDocument();
  });

  it("keeps System status in the footer", () => {
    render(<LandingFooter />);
    expect(screen.getByRole("link", { name: "System status" })).toHaveAttribute("href", "/status");
  });
});

describe("hero", () => {
  it("keeps one headline and all three beats readable, beat 1 shown first", () => {
    render(<Hero signedIn={false} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Stop choosing between your work and your phone",
    );
    const beatOf = (text: RegExp) => screen.getByText(text).closest("[data-active]");
    expect(beatOf(/answers calls and chats, and books real appointments/)).toHaveAttribute(
      "data-active",
      "true",
    );
    expect(beatOf(/Every call answered/)).toHaveAttribute("data-active", "false");
    expect(beatOf(/once your Google Calendar confirms/)).toHaveAttribute("data-active", "false");
    expect(screen.getByRole("link", { name: "Start free trial" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See how it works" })).toHaveAttribute(
      "href",
      "#how-it-works",
    );
  });
});

describe("how it works and journey", () => {
  it("lists the three jobs in order", () => {
    render(<HowItWorks />);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Answers calls and chats",
      "Books real appointments",
      "Escalates what it can't handle",
    ]);
  });

  it("walks from sign-up to going live in four steps", () => {
    render(<Journey />);
    const list = screen.getByRole("list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
    expect(within(list).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Start the trial",
      "Teach it your business",
      "Connect Google Calendar",
      "Go live",
    ]);
  });
});

describe("ScrollReveal", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.removeAttribute("data-reveal-armed");
  });

  function stubMotion(reduce: boolean) {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: reduce && q.includes("reduce") }));
  }

  it("never hides anything under reduced motion", () => {
    stubMotion(true);
    render(
      <>
        <div data-reveal>Below</div>
        <ScrollReveal />
      </>,
    );
    expect(document.documentElement).not.toHaveAttribute("data-reveal-armed");
  });

  it("marks on-screen blocks revealed before arming, and observes the rest", () => {
    stubMotion(false);
    const observed: Element[] = [];
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        observe(el: Element) {
          observed.push(el);
        }
        unobserve() {}
        disconnect() {}
      },
    );
    // happy-dom lays nothing out, so place the two blocks by hand before arming.
    const block = (top: number) => {
      const el = document.createElement("div");
      el.setAttribute("data-reveal", "");
      el.getBoundingClientRect = () => ({ top }) as DOMRect;
      document.body.append(el);
      return el;
    };
    const above = block(100);
    const below = block(window.innerHeight * 2);
    render(<ScrollReveal />);

    expect(above).toHaveAttribute("data-revealed");
    expect(below).not.toHaveAttribute("data-revealed");
    expect(observed).toEqual([below]);
    expect(document.documentElement).toHaveAttribute("data-reveal-armed");
    above.remove();
    below.remove();
  });
});

describe("SmartHeader", () => {
  let frames: FrameRequestCallback[] = [];

  beforeEach(() => {
    frames = [];
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
  });

  const setScrollY = (y: number) =>
    Object.defineProperty(window, "scrollY", { value: y, configurable: true });

  afterEach(() => {
    vi.unstubAllGlobals();
    setScrollY(0);
  });

  function scrollTo(y: number) {
    act(() => {
      setScrollY(y);
      window.dispatchEvent(new Event("scroll"));
      const pending = frames;
      frames = [];
      pending.forEach((f) => f(0));
    });
  }

  it("hides on scroll down past the hero and returns on scroll up", () => {
    render(<LandingHeader signedIn={false} />);
    const header = screen.getByRole("banner");
    scrollTo(900);
    expect(header).toHaveAttribute("data-hidden", "true");
    scrollTo(700);
    expect(header).toHaveAttribute("data-hidden", "false");
  });

  it("stays put while focus is inside it", () => {
    render(<LandingHeader signedIn={false} />);
    const header = screen.getByRole("banner");
    act(() => screen.getByRole("link", { name: "Sign in" }).focus());
    scrollTo(900);
    expect(header).toHaveAttribute("data-hidden", "false");
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
