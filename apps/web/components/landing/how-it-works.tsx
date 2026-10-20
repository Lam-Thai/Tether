import { CalendarCheck, MessageSquareText, PhoneCall } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Eyebrow } from "@/components/ui/eyebrow";
import { cn } from "@/lib/utils";

type Step = {
  icon: LucideIcon;
  title: string;
  body: string;
  accent: string;
};

const steps: Step[] = [
  {
    icon: PhoneCall,
    title: "Answers calls and chats",
    body: "Every call and chat message gets a reply, day or night. Tether asks what the customer needs and what time suits them.",
    accent: "bg-brand-yellow",
  },
  {
    icon: CalendarCheck,
    title: "Books real appointments",
    body: "Tether checks your Google Calendar and only tells a customer they are booked once the calendar confirms the slot.",
    accent: "bg-brand-green",
  },
  {
    icon: MessageSquareText,
    title: "Escalates what it can't handle",
    body: "If a question is outside what Tether can answer, it sends you a text message with the details so you can step in.",
    accent: "bg-brand-blue",
  },
];

export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="mx-auto w-full max-w-[1280px] scroll-mt-20 px-6 py-[clamp(4rem,8vw,8rem)]"
    >
      <div className="mb-12 flex max-w-2xl flex-col gap-3">
        <Eyebrow>How it works</Eyebrow>
        <h2 className="text-h1 font-bold tracking-tight uppercase">
          Three jobs, handled while you work
        </h2>
      </div>
      <ol className="flex flex-col gap-10">
        {steps.map((step, index) => {
          const Icon = step.icon;
          const flip = index % 2 === 1;
          return (
            <li
              key={step.title}
              className={cn(
                "animate-enter grid grid-cols-1 items-center gap-6 md:gap-12",
                flip ? "md:grid-cols-[1fr_2fr]" : "md:grid-cols-[2fr_1fr]",
              )}
              style={{ "--i": index } as React.CSSProperties}
            >
              <div className={cn("flex flex-col gap-3", flip && "md:order-2")}>
                <h3 className="text-h2 font-bold tracking-tight uppercase">{step.title}</h3>
                <p className="max-w-[56ch] text-base text-muted-foreground">{step.body}</p>
              </div>
              <div
                aria-hidden
                className={cn(
                  "relative flex h-32 items-center justify-center overflow-hidden rounded-md bg-surface-dark md:h-40",
                  flip && "md:order-1",
                )}
              >
                <div className={cn("absolute -right-6 -bottom-10 size-32 rotate-12", step.accent)} />
                <Icon className="relative size-12 text-surface-dark-foreground" strokeWidth={1.5} />
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
