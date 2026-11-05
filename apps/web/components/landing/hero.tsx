import Link from "next/link";

import { HeroStage, type HeroBeat } from "@/components/landing/hero-stage";
import { heroFrames } from "@/components/landing/photos";
import { PrimaryCta } from "@/components/landing/primary-cta";
import { buttonVariants } from "@/components/ui/button";

const beats: HeroBeat[] = [
  {
    headline: "Stop choosing between your work and your phone",
    body: "Tether answers calls and chats, and books real appointments for you.",
  },
  {
    headline: "Every call answered, day or night",
    body: "It covers your front desk on the phone and in chat while you work.",
  },
  {
    headline: "Booked only when your calendar says yes",
    body: "A customer hears they are booked once your Google Calendar confirms the slot.",
  },
];

export function Hero({ signedIn }: { signedIn: boolean }) {
  return (
    <HeroStage
      beats={beats}
      frames={heroFrames}
      actions={
        <>
          <PrimaryCta signedIn={signedIn} />
          <Link href="#how-it-works" className={buttonVariants({ variant: "inverse" })}>
            See how it works
          </Link>
        </>
      }
    />
  );
}
