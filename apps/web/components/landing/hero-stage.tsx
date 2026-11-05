"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";

import type { LandingPhoto } from "@/components/landing/photos";
import {
  approach,
  driveWhileVisible,
  frameLayer,
  pinProgress,
  prefersReducedMotion,
  stepFor,
} from "@/lib/motion/scroll-driver";
import { cn } from "@/lib/utils";

export type HeroBeat = { headline: string; body: string };

/** Pin progress at which the last frame is fully in. */
const FRAMES_DONE_AT = 0.88;

/**
 * Full-bleed pinned hero (DESIGN.md Resolved decisions 16 and 18). The section is three
 * screens tall and its stage sticks to the top of the viewport, so the first few scrolls
 * stay on the hero: scroll progress scrubs through the photo sequence (each frame zooms
 * out and dissolves into the next) and steps the headline through the beats. Beat 1's
 * headline is the page's h1; the actions never change. Reduced motion and no-JS get a
 * one-screen hero on frame 1 and beat 1. Per-frame work writes styles directly (no React
 * state); only the beat index is state.
 */
export function HeroStage({
  beats,
  frames,
  actions,
}: {
  beats: HeroBeat[];
  frames: LandingPhoto[];
  actions: React.ReactNode;
}) {
  const sectionRef = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const layerRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [beat, setBeat] = useState(0);

  useEffect(() => {
    const section = sectionRef.current;
    const stage = stageRef.current;
    if (!section || !stage || prefersReducedMotion()) return;

    let current = 0;
    let shown = 0;
    return driveWhileVisible(section, () => {
      const target = pinProgress({
        top: section.getBoundingClientRect().top,
        height: section.offsetHeight,
        stageHeight: stage.offsetHeight,
        stickyTop: 0,
      });
      current = approach(current, target, 0.12);
      section.style.setProperty("--hero-p", current.toFixed(4));
      // The sequence completes a little before the pin ends, so the last frame holds briefly.
      const framesProgress = Math.min(1, current / FRAMES_DONE_AT);
      layerRefs.current.forEach((layer, i) => {
        if (!layer) return;
        const { opacity, scale } = frameLayer(framesProgress, i, frames.length);
        layer.style.opacity = opacity.toFixed(3);
        layer.style.transform = `scale(${scale.toFixed(4)})`;
      });
      const next = stepFor(current, beats.length);
      if (next !== shown) {
        shown = next;
        setBeat(next);
      }
      return current !== target;
    });
  }, [beats.length, frames.length]);

  return (
    // -mt-16 tucks the stage under the 4rem sticky header, so the photo runs edge to edge.
    <section ref={sectionRef} className="relative -mt-16 bg-surface-dark motion-safe:h-[300dvh]">
      <div ref={stageRef} className="sticky top-0 isolate h-[100dvh] overflow-hidden">
        <div aria-hidden className="absolute inset-0 -z-10">
          {frames.map((frame, i) => (
            <div
              key={i}
              ref={(el) => {
                layerRefs.current[i] = el;
              }}
              className="absolute inset-0 will-change-[opacity,transform]"
              style={{ opacity: i === 0 ? 1 : 0, transform: `scale(${1 + 0.14 * (i === 0 ? 0.57 : 1)})` }}
            >
              <Image
                src={frame.src}
                alt=""
                fill
                priority={i === 0}
                sizes="100vw"
                className="object-cover"
                style={{ objectPosition: frame.position }}
              />
            </div>
          ))}
          {/* Solid scrim (no gradients): keeps white copy at AA over the brightest frames. */}
          <div className="absolute inset-0 bg-surface-dark/60" />
          <div
            className="absolute top-[16%] right-[6%] size-14 rounded-full bg-brand-yellow md:size-20"
            style={{ transform: "translate3d(0, calc(var(--hero-p, 0) * -2.5rem), 0)" }}
          />
          <div
            className="absolute top-[9%] right-[-5rem] h-3 w-1/2 -rotate-[24deg] bg-brand-blue md:h-4 md:w-1/4"
            style={{ transform: "translate3d(calc(var(--hero-p, 0) * -6rem), 0, 0)" }}
          />
        </div>

        <div className="mx-auto flex h-full w-full max-w-[1280px] flex-col justify-end gap-6 px-6 pt-24 pb-[clamp(2.5rem,8dvh,5rem)] text-surface-dark-foreground">
          <span aria-hidden className="block h-2 w-16 -skew-x-12 bg-brand-red" />
          {/* Beats share one grid cell, so the block is as tall as the longest beat and the
              actions below never move. All stay in the accessibility tree, in order. */}
          <div className="animate-enter grid">
            {beats.map((b, i) => {
              const Headline = i === 0 ? "h1" : "p";
              return (
                <div
                  key={b.headline}
                  data-active={i === beat}
                  // The outgoing beat clears in 200ms before the incoming one rises in, so
                  // two headlines never sit on top of each other mid-swap.
                  className="flex flex-col gap-4 transition-[opacity,translate] duration-200 ease-(--ease-out-quick) [grid-area:1/1] data-[active=false]:-translate-y-2 data-[active=false]:opacity-0 data-[active=true]:delay-200 data-[active=true]:duration-[420ms] motion-reduce:transition-none"
                >
                  <Headline className="max-w-4xl text-[clamp(2.5rem,5.5vw,4.5rem)] leading-[1.02] font-bold tracking-tight uppercase">
                    {b.headline}
                  </Headline>
                  <p className="max-w-[48ch] text-lg text-surface-dark-foreground/90">{b.body}</p>
                </div>
              );
            })}
          </div>
          <div
            className="animate-enter flex flex-wrap items-center gap-3"
            style={{ "--i": 1 } as React.CSSProperties}
          >
            {actions}
          </div>
          <div aria-hidden className="hidden gap-2 motion-safe:flex">
            {beats.map((b, i) => (
              <span key={b.headline} className="relative h-1 w-10 overflow-hidden bg-surface-dark-foreground/30">
                <span
                  className={cn(
                    "absolute inset-0 origin-left bg-surface-dark-foreground transition-transform duration-[420ms] ease-(--ease-out-quick)",
                    i === beat ? "scale-x-100" : "scale-x-0",
                  )}
                />
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
