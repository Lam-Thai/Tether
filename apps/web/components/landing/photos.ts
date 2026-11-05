import type { StaticImageData } from "next/image";

import heroBell from "@/public/landing/hero-bell.webp";
import heroCafeRobot from "@/public/landing/hero-cafe-robot.webp";
import heroFrontDesk from "@/public/landing/hero-front-desk.webp";
import heroHandshake from "@/public/landing/hero-handshake.webp";
import heroRobot from "@/public/landing/hero-robot.webp";
import storyBooking from "@/public/landing/story-booking.webp";
import storyCalls from "@/public/landing/story-calls.webp";
import storyText from "@/public/landing/story-text.webp";

/**
 * Landing photography: Unsplash License photos, baked to grayscale WebP (DESIGN.md
 * Resolved decision 17). Credits and sources: `public/landing/CREDITS.md`. Every photo is
 * decorative (the copy beside it carries the meaning), so each renders with `alt=""`.
 */
export type LandingPhoto = { src: StaticImageData | string; position?: string };

/** The hero's scroll-scrubbed sequence, in order: a front desk waiting, then the robots arrive. */
export const heroFrames: LandingPhoto[] = [
  { src: heroBell, position: "60% 50%" },
  { src: heroFrontDesk, position: "70% 50%" },
  { src: heroRobot, position: "55% 40%" },
  { src: heroCafeRobot, position: "65% 50%" },
  { src: heroHandshake, position: "50% 35%" },
];

export const storyPhotos = {
  calls: { src: storyCalls, position: "38% 50%" },
  booking: { src: storyBooking, position: "50% 50%" },
  text: { src: storyText, position: "45% 40%" },
} satisfies Record<string, LandingPhoto>;
