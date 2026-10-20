import Link from "next/link";

import type { BusinessResult } from "@/lib/api/client";
import { buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Eyebrow } from "@/components/ui/eyebrow";

interface BusinessOverviewProps {
  result: BusinessResult;
}

/**
 * Pure presentational rendering of a `BusinessResult`, kept separate from
 * `app/dashboard/page.tsx` (which does the fetch) so each state is
 * unit-testable without a Server Component boundary, same pattern as
 * `components/status-view.tsx`.
 */
export function BusinessOverview({ result }: BusinessOverviewProps) {
  if (result.kind === "not_found") {
    return (
      <Callout
        tone="warning"
        title="No business yet"
        headingLevel={1}
        action={
          <Link href="/onboarding" className={buttonVariants({ size: "sm" })}>
            Finish onboarding
          </Link>
        }
      >
        Finish onboarding to see your business details here.
      </Callout>
    );
  }

  if (result.kind === "unauthorized") {
    return (
      <Callout
        tone="danger"
        title="Your session has expired"
        headingLevel={1}
        action={
          <Link href="/sign-in" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Sign in
          </Link>
        }
      >
        Sign in again to see your business details.
      </Callout>
    );
  }

  if (result.kind === "unreachable") {
    return (
      <Callout tone="danger" title="Can't load your business" headingLevel={1}>
        {result.reason}
      </Callout>
    );
  }

  const { data } = result;

  return (
    <Card className="overflow-hidden">
      <div aria-hidden className="h-2 bg-brand-red [clip-path:polygon(0_0,100%_0,92%_100%,0_100%)]" />
      <CardHeader>
        <Eyebrow>Business</Eyebrow>
        <CardTitle as="h1" className="text-h1">{data.name}</CardTitle>
        <CardDescription>{data.industry}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-x-8 gap-y-5 md:grid-cols-[3fr_2fr]">
          <Field label="Address" value={data.address} />
          <Field label="Timezone" value={data.timezone} mono />
          <Field label="Phone" value={data.phone ?? "Not set"} mono={data.phone !== null} />
          <Field label="Contact email" value={data.contact_email ?? "Not set"} />
        </dl>
      </CardContent>
    </Card>
  );
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-sm font-medium tracking-[0.08em] text-muted-foreground uppercase">{label}</dt>
      <dd className={mono ? "font-mono text-base text-foreground" : "text-base text-foreground"}>
        {value}
      </dd>
    </div>
  );
}
