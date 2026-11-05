"use client";

import { useEffect } from "react";

import { prefersReducedMotion } from "@/lib/motion/scroll-driver";

/** Fraction of the viewport height an element must be above to count as already seen. */
const SEEN_LINE = 0.92;

/**
 * Arms the scroll reveal for every `[data-reveal]` on the page (CSS in `app/globals.css`).
 * Blocks already on screen are marked revealed first so nothing visible blinks out; the rest
 * are revealed once, as they enter the viewport. Reduced motion or no IntersectionObserver:
 * nothing is armed and everything stays visible. Renders nothing.
 */
export function ScrollReveal() {
  useEffect(() => {
    if (prefersReducedMotion() || typeof IntersectionObserver !== "function") return;

    const root = document.documentElement;
    const reveal = (el: Element) => el.setAttribute("data-revealed", "");
    const pending: Element[] = [];
    for (const el of document.querySelectorAll("[data-reveal]")) {
      if (el.getBoundingClientRect().top < window.innerHeight * SEEN_LINE) reveal(el);
      else pending.push(el);
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          reveal(entry.target);
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: `0px 0px -${Math.round((1 - SEEN_LINE) * 100)}% 0px` },
    );
    pending.forEach((el) => observer.observe(el));
    root.setAttribute("data-reveal-armed", "");

    return () => {
      observer.disconnect();
      root.removeAttribute("data-reveal-armed");
    };
  }, []);

  return null;
}
