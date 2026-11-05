const steps = [
  {
    title: "Start the trial",
    body: "Create your account. The first 14 days are free and need no card.",
  },
  {
    title: "Teach it your business",
    body: "Add your services, opening hours and answers to the questions customers ask most.",
  },
  {
    title: "Connect Google Calendar",
    body: "Tether checks your calendar before it offers a time, and books straight into it.",
  },
  {
    title: "Go live",
    body: "Try it in the test chat, then switch it on. You get a text when a customer needs you.",
  },
];

/**
 * From sign-up to the first booking, as a timeline. When it scrolls into view the rule draws
 * across (down, on phones) and each step's marker lights in turn: CSS in `app/globals.css`
 * keyed off `data-revealed`, set by `<ScrollReveal />`. Static and fully lit without JS or
 * with reduced motion.
 */
export function Journey() {
  return (
    <section
      aria-labelledby="journey-title"
      className="mx-auto w-full max-w-[1280px] px-6 pb-[clamp(4rem,8vw,8rem)]"
    >
      <h2
        id="journey-title"
        data-reveal
        className="mb-12 max-w-2xl text-h1 font-bold tracking-tight uppercase"
      >
        From sign-up to your first booking
      </h2>
      <ol data-reveal className="relative grid grid-cols-1 gap-10 md:grid-cols-4 md:gap-8">
        <span
          aria-hidden
          className="journey-line absolute top-0 bottom-0 left-5 w-0.5 bg-foreground md:top-5 md:right-0 md:bottom-auto md:left-0 md:h-0.5 md:w-auto"
        />
        {steps.map((step, i) => (
          <li key={step.title} className="relative flex gap-5 md:flex-col">
            <span
              aria-hidden
              className="relative flex size-10 shrink-0 items-center justify-center rounded-sm border-2 border-foreground bg-background font-mono text-sm font-medium"
            >
              <span
                className="journey-mark absolute inset-1 -rotate-12 bg-brand-red"
                style={{ "--i": i } as React.CSSProperties}
              />
              <span className="relative">{i + 1}</span>
            </span>
            <div className="flex flex-col gap-2">
              <h3 className="text-h2 font-bold tracking-tight uppercase">{step.title}</h3>
              <p className="max-w-[36ch] text-base text-muted-foreground">{step.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
