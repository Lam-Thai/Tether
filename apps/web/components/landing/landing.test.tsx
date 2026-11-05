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
import { slideIn } from "@/lib/motion/scroll-driver";

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
    const unobserved: Element[] = [];
    let onIntersect: ((entries: Partial<IntersectionObserverEntry>[]) => void) | null = null;
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(cb: (entries: Partial<IntersectionObserverEntry>[]) => void) {
          onIntersect = cb;
        }
        observe(el: Element) {
          observed.push(el);
        }
        unobserve(el: Element) {
          unobserved.push(el);
        }
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

    // Scrolling near it without entering the band reveals nothing; entering reveals it once.
    act(() => onIntersect?.([{ target: below, isIntersecting: false }]));
    expect(below).not.toHaveAttribute("data-revealed");
    act(() => onIntersect?.([{ target: below, isIntersecting: true }]));
    expect(below).toHaveAttribute("data-revealed");
    expect(unobserved).toEqual([below]);
    above.remove();
    below.remove();
  });
});

describe("motion-driven landing sections", () => {
  type Entry = Partial<IntersectionObserverEntry>;
  let observers: {
    cb: (entries: Entry[]) => void;
    targets: Element[];
    options?: IntersectionObserverInit;
  }[];
  let frames: FrameRequestCallback[];

  beforeEach(() => {
    observers = [];
    frames = [];
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        targets: Element[] = [];
        constructor(cb: (entries: Entry[]) => void, options?: IntersectionObserverInit) {
          observers.push({ cb, targets: this.targets, options });
        }
        observe(el: Element) {
          this.targets.push(el);
        }
        unobserve() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const reduceMotion = (reduce: boolean) =>
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: reduce && q.includes("reduce") }));

  function runFrames(limit = 400) {
    for (let i = 0; i < limit && frames.length; i++) {
      const next = frames.shift();
      act(() => next?.(0));
    }
  }

  describe("story rows", () => {
    let resized: (() => void) | null;

    beforeEach(() => {
      resized = null;
      vi.stubGlobal(
        "ResizeObserver",
        class {
          constructor(cb: () => void) {
            resized = cb;
          }
          observe() {}
          disconnect() {}
        },
      );
    });

    // happy-dom lays nothing out, so each row's photo and copy are placed by hand.
    type Box = { media: number; copy: number; mediaHeight?: number };
    function setup() {
      reduceMotion(false);
      const { container } = render(<HowItWorks />);
      const list = container.querySelector("ol")!;
      const rows = Array.from(container.querySelectorAll<HTMLElement>("[data-step]"));
      const place = (boxes: Box[]) =>
        rows.forEach((row, i) => {
          const { media, copy, mediaHeight = 400 } = boxes[i]!;
          row.querySelector<HTMLElement>(".story-media")!.getBoundingClientRect = () =>
            ({ top: media, bottom: media + mediaHeight }) as DOMRect;
          row.querySelector<HTMLElement>(".story-copy")!.getBoundingClientRect = () =>
            ({ top: copy, bottom: copy + 200 }) as DOMRect;
        });
      const story = observers.find((o) => o.targets.includes(list));
      // An absent property reads as NaN, so a row that lost its progress can never pass as 0 or 1.
      const read = (row: HTMLElement, name: "--media-p" | "--copy-p") => {
        const raw = row.style.getPropertyValue(name);
        return raw === "" ? NaN : Number(raw);
      };
      return { list, rows, place, story, read };
    }

    it("each row slides in from its position and rewinds on scroll up", () => {
      const { list, rows, place, story, read } = setup();
      const vh = window.innerHeight;
      // Side by side (copy beside the photo): row 0 well into view, row 1 half way, row 2 below.
      const side = (top: number) => ({ media: top, copy: top + 100 });
      place([side(vh * 0.2), side(vh * 0.75), side(vh * 1.5)]);
      act(() => story?.cb([{ target: list, isIntersecting: true }]));
      runFrames();

      expect(read(rows[0]!, "--media-p")).toBe(1);
      expect(read(rows[0]!, "--copy-p")).toBe(1);
      // Beside the photo, the copy is scrubbed from the photo's top and trails it.
      const half = slideIn(vh * 0.75, vh);
      expect(read(rows[1]!, "--media-p")).toBeCloseTo(half.media, 3);
      expect(read(rows[1]!, "--copy-p")).toBeCloseTo(half.copy, 3);
      expect(read(rows[2]!, "--media-p")).toBe(0);
      expect(read(rows[2]!, "--copy-p")).toBe(0);

      // Scrolling back up pushes row 0 down again, and both halves slide back out.
      place([side(vh * 1.1), side(vh * 1.6), side(vh * 2.4)]);
      act(() => window.dispatchEvent(new Event("scroll")));
      runFrames();
      expect(read(rows[0]!, "--media-p")).toBe(0);
      expect(read(rows[0]!, "--copy-p")).toBe(0);
    });

    it("stacked (phones): the copy is scrubbed from its own top, below the photo", () => {
      const { list, rows, place, story, read } = setup();
      const vh = window.innerHeight;
      // The photo has fully landed, but the copy under it is only just entering the viewport.
      const stacked = { media: vh * 0.1, copy: vh * 0.95, mediaHeight: vh * 0.7 };
      place([stacked, stacked, stacked]);
      act(() => story?.cb([{ target: list, isIntersecting: true }]));
      runFrames();

      expect(read(rows[0]!, "--media-p")).toBe(1);
      expect(read(rows[0]!, "--copy-p")).toBeCloseTo(slideIn(vh * 0.95, vh).copy, 3);
      expect(read(rows[0]!, "--copy-p")).toBeLessThan(1);
    });

    it("repaints when the list reflows without a scroll", () => {
      const { list, rows, place, story, read } = setup();
      const vh = window.innerHeight;
      place([{ media: vh * 1.2, copy: vh * 1.3 }, { media: vh * 2, copy: vh * 2.1 }, { media: vh * 3, copy: vh * 3.1 }]);
      act(() => story?.cb([{ target: list, isIntersecting: true }]));
      runFrames();
      expect(read(rows[0]!, "--media-p")).toBe(0);

      // Something above shrinks: row 0 is now in view, with no scroll or resize event.
      place([{ media: vh * 0.2, copy: vh * 0.3 }, { media: vh, copy: vh * 1.1 }, { media: vh * 2, copy: vh * 2.1 }]);
      act(() => resized?.());
      runFrames();
      expect(read(rows[0]!, "--media-p")).toBe(1);
    });
  });

  it("story: under reduced motion nothing is driven and every row stays in place", () => {
    reduceMotion(true);
    const { container } = render(<HowItWorks />);
    const list = container.querySelector("ol")!;
    expect(observers.some((o) => o.targets.includes(list))).toBe(false);
    for (const row of container.querySelectorAll<HTMLElement>("[data-step]")) {
      expect(row.style.getPropertyValue("--media-p")).toBe("");
    }
  });

  it("hero: scrolling through the pin scrubs the frames and steps the beats", () => {
    reduceMotion(false);
    const { container } = render(<Hero signedIn={false} />);
    const section = container.querySelector("section")!;
    const stage = section.firstElementChild as HTMLElement;
    expect(section).toHaveAttribute("data-pinned");

    // Three screens of section, one screen of stage, scrolled to the end of the pin.
    Object.defineProperty(section, "offsetHeight", { value: 2400, configurable: true });
    Object.defineProperty(stage, "offsetHeight", { value: 800, configurable: true });
    section.getBoundingClientRect = () => ({ top: -1600 }) as DOMRect;
    const hero = observers.find((o) => o.targets.includes(section));
    act(() => hero?.cb([{ target: section, isIntersecting: true }]));
    runFrames();

    const beatOf = (text: RegExp) => screen.getByText(text).closest("[data-active]");
    expect(beatOf(/once your Google Calendar confirms/)).toHaveAttribute("data-active", "true");
    expect(beatOf(/Every call answered/)).toHaveAttribute("data-active", "false");
    const layers = Array.from(section.querySelectorAll<HTMLElement>(".will-change-\\[opacity\\,transform\\]"));
    expect(layers.at(-1)?.style.opacity).toBe("1.000");
    expect(layers.at(-1)?.style.transform).not.toBe("scale(1.14)");
  });

  it("hero: under reduced motion nothing pins or is observed", () => {
    reduceMotion(true);
    const { container } = render(<Hero signedIn={false} />);
    const section = container.querySelector("section")!;
    expect(section).not.toHaveAttribute("data-pinned");
    expect(observers.some((o) => o.targets.includes(section))).toBe(false);
  });

  it("header: never hides under reduced motion", () => {
    reduceMotion(true);
    render(<LandingHeader signedIn={false} />);
    act(() => {
      Object.defineProperty(window, "scrollY", { value: 900, configurable: true });
      window.dispatchEvent(new Event("scroll"));
    });
    runFrames();
    expect(screen.getByRole("banner")).toHaveAttribute("data-hidden", "false");
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
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
