"use client";

import Image from "next/image";
import { CalendarCheck, MessageSquareText, PhoneCall } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef } from "react";

import { storyPhotos, type LandingPhoto } from "@/components/landing/photos";
import { Eyebrow } from "@/components/ui/eyebrow";
import { driveWhileVisible, slideIn, whileMotionAllowed } from "@/lib/motion/scroll-driver";
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
 * Story rows (DESIGN.md Resolved decision 16). Each step is a row: photo left, copy right
 * (stacked on phones). As a row rises into view the photo slides in from the left and the
 * copy from the right, scrubbed straight from the row's position (`slideIn`), so scrolling
 * back up plays it in reverse. Progress lands in `--media-p` / `--copy-p` on each row and
 * `app/globals.css` turns it into `translate` + `opacity`. Without JS, or under reduced
 * motion, nothing is driven and every row sits in place, fully visible.
 */
export function HowItWorks() {
  const listRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const rows = Array.from(list.querySelectorAll<HTMLElement>("[data-step]"));

    return whileMotionAllowed(() => {
      const paint = () => {
        const viewport = window.innerHeight;
        for (const row of rows) {
          const { media, copy } = slideIn(row.getBoundingClientRect().top, viewport);
          row.style.setProperty("--media-p", media.toFixed(4));
          row.style.setProperty("--copy-p", copy.toFixed(4));
        }
      };
      // Place every row now, so nothing shows at full strength for a frame before the driver runs.
      paint();
      const stop = driveWhileVisible(list, () => {
        paint();
        return false;
      });
      return () => {
        stop();
        for (const row of rows) {
          row.style.removeProperty("--media-p");
          row.style.removeProperty("--copy-p");
        }
      };
    });
  }, []);

  return (
    <section
      id="how-it-works"
      className="mx-auto w-full max-w-[1280px] scroll-mt-28 overflow-x-clip px-6 py-[clamp(4rem,8vw,8rem)]"
    >
      <div data-reveal className="mb-12 flex max-w-2xl flex-col gap-3 md:mb-20">
        <Eyebrow>How it works</Eyebrow>
        <h2 className="text-h1 font-bold tracking-tight uppercase">
          Three jobs, handled while you work
        </h2>
      </div>
      <ol ref={listRef} className="flex flex-col gap-16 md:gap-28">
        {steps.map((step, index) => {
          const Icon = step.icon;
          return (
            <li
              key={step.title}
              data-step={index}
              className="grid grid-cols-1 items-center gap-8 md:grid-cols-[5fr_6fr] md:gap-16"
            >
              <div
                aria-hidden
                className="story-media relative isolate aspect-[4/3] overflow-hidden rounded-lg bg-surface-dark"
              >
                <Image
                  src={step.photo.src}
                  alt=""
                  fill
                  sizes="(min-width: 768px) 40vw, 90vw"
                  className="-z-10 object-cover"
                  style={{ objectPosition: step.photo.position }}
                />
                {/* The step's accent shape cuts in from the corner as the photo lands. */}
                <div
                  className={cn(
                    "story-accent absolute -right-8 -bottom-12 size-36 rotate-12 md:size-44",
                    step.accent,
                  )}
                />
              </div>
              <div className="story-copy relative flex flex-col gap-3 pl-6 md:pl-8">
                {/* A red rule that draws down the copy's left edge as it slides in. */}
                <span aria-hidden className="story-mark absolute inset-y-0 left-0 w-1 bg-brand-red" />
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
    </section>
  );
}
