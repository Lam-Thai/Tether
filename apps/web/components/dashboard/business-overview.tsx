import type { BusinessResult } from "@/lib/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Eyebrow } from "@/components/ui/eyebrow";

interface BusinessOverviewProps {
  result: BusinessResult;
}

/**
 * Pure presentational rendering of a `BusinessResult`, kept separate from
 * `app/dashboard/page.tsx` (which does the fetch) so each state is
 * unit-testable without a Server Component boundary — same pattern as
 * `components/status-view.tsx`.
 */
export function BusinessOverview({ result }: BusinessOverviewProps) {
  if (result.kind === "not_found") {
    return (
      <Card className="border-warn-border bg-warn">
        <CardHeader>
          <Eyebrow className="text-warn-foreground/70">Business</Eyebrow>
          <CardTitle className="text-warn-foreground">No business yet</CardTitle>
          <CardDescription className="text-warn-foreground/80">
            Finish onboarding to see your business details here.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (result.kind === "unauthorized") {
    return (
      <Card className="border-danger-border bg-danger">
        <CardHeader>
          <Eyebrow className="text-danger-foreground/70">Business</Eyebrow>
          <CardTitle className="text-danger-foreground">Your session has expired</CardTitle>
          <CardDescription className="text-danger-foreground/80">
            Sign in again to see your business details.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (result.kind === "unreachable") {
    return (
      <Card className="border-danger-border bg-danger">
        <CardHeader>
          <Eyebrow className="text-danger-foreground/70">Business</Eyebrow>
          <CardTitle className="text-danger-foreground">Can&apos;t load your business</CardTitle>
          <CardDescription className="text-danger-foreground/80">{result.reason}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const { data } = result;

  return (
    <Card>
      <CardHeader>
        <Eyebrow>Business</Eyebrow>
        <CardTitle>{data.name}</CardTitle>
        <CardDescription>{data.industry}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Address" value={data.address} />
          <Field label="Timezone" value={data.timezone} mono />
          <Field label="Phone" value={data.phone ?? "Not set"} />
          <Field label="Contact email" value={data.contact_email ?? "Not set"} />
        </dl>
      </CardContent>
    </Card>
  );
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="font-mono text-xs tracking-[0.15em] text-muted-foreground uppercase">{label}</dt>
      <dd className={mono ? "font-mono text-sm text-foreground" : "text-sm text-foreground"}>{value}</dd>
    </div>
  );
}
