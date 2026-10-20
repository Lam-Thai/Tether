import type { Metadata } from "next";
import { auth } from "@clerk/nextjs/server";

import { ClosingCta, LandingFooter } from "@/components/landing/closing-footer";
import { Hero } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { LandingHeader } from "@/components/landing/landing-header";
import { Pricing } from "@/components/landing/pricing";

const description =
  "Tether answers calls and chats, books real appointments into your Google Calendar, and texts you what it cannot handle.";

export const metadata: Metadata = {
  description,
  openGraph: {
    title: "Tether: AI receptionist for small businesses",
    description,
    type: "website",
  },
};

// Static marketing content; the only per-request input is whether a Clerk
// session exists, which swaps the signup CTA for "Go to dashboard".
export default async function LandingPage() {
  const { userId } = await auth();
  const signedIn = userId !== null;

  return (
    <>
      <LandingHeader signedIn={signedIn} />
      <main>
        <Hero signedIn={signedIn} />
        <HowItWorks />
        <Pricing signedIn={signedIn} />
        <ClosingCta signedIn={signedIn} />
      </main>
      <LandingFooter />
    </>
  );
}
