"use client";

import Image from "next/image";
import { CalendarCheck, MessageSquareText, PhoneCall } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { storyPhotos, type LandingPhoto } from "@/components/landing/photos";
import { Eyebrow } from "@/components/ui/eyebrow";
import { cn } from "@/lib/utils";

type Step = {
  icon: LucideIcon;
  title: string;
  body: string;
  accent: string;
  photo: LandingPhoto;
};

const steps: Step[] = [
  {
    icon: PhoneCall,
    title: "Answers calls and chats",
    body: "Every call and chat message gets a reply, day or night. Tether asks what the customer needs and what time suits them.",
    accent: "bg-brand-yellow",
    photo: storyPhotos.calls,
  },
  {
    icon: CalendarCheck,
    title: "Books real appointments",
    body: "Tether checks your Google Calendar and only tells a customer they are booked once the calendar confirms the slot.",
    accent: "bg-brand-green",
    photo: storyPhotos.booking,
  },
  {
    icon: MessageSquareText,
    title: "Escalates what it can't handle",
    body: "If a question is outside what Tether can answer, it sends you a text message with the details so you can step in.",
    accent: "bg-brand-blue",
    photo: storyPhotos.text,
  },
];

/**
 * Sticky story (DESIGN.md Resolved decision 16). From md up the photo column stays pinned
 * while the steps scroll past; the step crossing the middle of the viewport becomes active,
 * swapping the photo and its accent shape and drawing the step's marker. Phones get each
 * photo inline above its step instead; so does reduced motion at every width (nothing pins,
 * each step sits beside its own photo).
 */
export function HowItWorks() {
  const listRef = useRef<HTMLOListElement>(null);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const list = listRef.current;
    if (!list || typeof IntersectionObserver !== "function") return;
    const items = Array.from(list.querySelectorAll<HTMLElement>("[data-step]"));
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(Number(entry.target.getAttribute("data-step")));
        }
      },
      // A thin band across the middle of the viewport.
      { rootMargin: "-45% 0px -45% 0px" },
    );
    items.forEach((item) => observer.observe(item));
    return () => observer.disconnect();
  }, []);

  return (
    <section
      id="how-it-works"
      className="mx-auto w-full max-w-[1280px] scroll-mt-28 px-6 py-[clamp(4rem,8vw,8rem)]"
    >
      <div data-reveal className="mb-12 flex max-w-2xl flex-col gap-3">
        <Eyebrow>How it works</Eyebrow>
        <h2 className="text-h1 font-bold tracking-tight uppercase">
          Three jobs, handled while you work
        </h2>
      </div>
      <div className="grid grid-cols-1 gap-12 md:gap-16 md:motion-safe:grid-cols-[5fr_6fr]">
        <div aria-hidden className="hidden md:motion-safe:block">
          <div className="sticky top-24 h-[min(72dvh,36rem)] overflow-hidden rounded-lg bg-surface-dark">
            {steps.map((step, i) => (
              <StoryFrame key={step.title} step={step} on={i === active} />
            ))}
          </div>
        </div>
        <ol ref={listRef} className="flex flex-col gap-12 md:gap-0">
          {steps.map((step, index) => {
            const Icon = step.icon;
            const on = index === active;
            return (
              <li
                key={step.title}
                data-step={index}
                data-reveal
                className="flex flex-col gap-6 md:justify-center md:motion-safe:min-h-[min(72dvh,36rem)] md:motion-reduce:grid md:motion-reduce:grid-cols-[5fr_6fr] md:motion-reduce:items-center md:motion-reduce:gap-16"
              >
                <div
                  aria-hidden
                  className="relative h-56 overflow-hidden rounded-md bg-surface-dark md:motion-safe:hidden md:motion-reduce:h-72"
                >
                  <StoryFrame step={step} on />
                </div>
                <div className="relative flex flex-col gap-3 md:motion-safe:pl-8">
                  {/* Active-step marker: a red rule that draws down the step's left edge. */}
                  <span
                    aria-hidden
                    className={cn(
                      "absolute inset-y-0 left-0 hidden w-1 origin-top bg-brand-red transition-transform duration-[420ms] ease-(--ease-out-quick) motion-reduce:transition-none md:motion-safe:block",
                      on ? "scale-y-100" : "scale-y-0",
                    )}
                  />
                  <span
                    aria-hidden
                    className={cn(
                      "flex size-12 items-center justify-center rounded-sm text-foreground",
                      step.accent,
                    )}
                  >
                    <Icon className="size-6" strokeWidth={1.5} />
                  </span>
                  <h3 className="text-h2 font-bold tracking-tight uppercase">{step.title}</h3>
                  <p className="max-w-[56ch] text-base text-muted-foreground">{step.body}</p>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}

/** One step's B&W photo with its accent shape cutting in from the corner. */
function StoryFrame({ step, on }: { step: Step; on: boolean }) {
  return (
    <div
      className={cn(
        "absolute inset-0 isolate transition-opacity duration-[600ms] ease-(--ease-out-quick) motion-reduce:transition-none",
        on ? "opacity-100" : "opacity-0",
      )}
    >
      <Image
        src={step.photo.src}
        alt=""
        fill
        sizes="(min-width: 768px) 40vw, 90vw"
        className="-z-10 object-cover"
        style={{ objectPosition: step.photo.position }}
      />
      <div
        className={cn(
          "absolute -right-8 -bottom-12 size-36 rotate-12 transition-transform duration-[600ms] ease-(--ease-out-quick) motion-reduce:transition-none md:size-44",
          step.accent,
          on ? "translate-y-0" : "translate-y-16",
        )}
      />
    </div>
  );
}
