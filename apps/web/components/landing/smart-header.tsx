"use client";

import { useEffect, useRef, useState } from "react";

import { nextHeaderHidden, prefersReducedMotion } from "@/lib/motion/scroll-driver";
import { cn } from "@/lib/utils";

const OPTIONS = { threshold: 480, delta: 6 };

/**
 * The landing page's sticky header: it slides away while the visitor scrolls down past the
 * hero's first screen and returns on any scroll up. It never hides while focus is inside it
 * (keyboard users), and never under reduced motion. Only `translate` animates.
 */
export function SmartHeader({ children, className }: { children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLElement>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    const header = ref.current;
    if (!header || prefersReducedMotion()) return;

    let anchorY = window.scrollY;
    let isHidden = false;
    let raf = 0;
    const apply = (next: boolean) => {
      if (next === isHidden) return;
      isHidden = next;
      setHidden(next);
    };
    const update = () => {
      raf = 0;
      const y = window.scrollY;
      if (header.contains(document.activeElement)) return apply(false);
      apply(nextHeaderHidden(isHidden, anchorY, y, OPTIONS));
      // Only move the anchor once a decision-sized distance has been scrolled, so slow
      // scrolling still adds up to a direction change.
      if (Math.abs(y - anchorY) > OPTIONS.delta) anchorY = y;
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    const onFocus = () => apply(false);

    window.addEventListener("scroll", onScroll, { passive: true });
    header.addEventListener("focusin", onFocus);
    return () => {
      window.removeEventListener("scroll", onScroll);
      header.removeEventListener("focusin", onFocus);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <header
      ref={ref}
      data-hidden={hidden}
      className={cn(
        "transition-transform duration-200 ease-out data-[hidden=true]:-translate-y-full motion-reduce:transition-none",
        className,
      )}
    >
      {children}
    </header>
  );
}
