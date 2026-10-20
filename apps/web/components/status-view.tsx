import type { HealthResult } from "@/lib/api/client";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Eyebrow } from "@/components/ui/eyebrow";

interface StatusViewProps {
  result: HealthResult;
}

/**
 * Pure presentational rendering of a `HealthResult`. Kept separate from
 * `app/page.tsx` (which does the actual fetch) so the three states can be
 * unit-tested without a Server Component boundary.
 */
export function StatusView({ result }: StatusViewProps) {
  if (result.kind === "unreachable") {
    return (
      <PageShell>
        <Card className="border-danger-border bg-danger">
          <CardHeader>
            <Eyebrow className="text-danger-foreground/70">Tether status</Eyebrow>
            <CardTitle className="text-danger-foreground">Can&apos;t reach the API</CardTitle>
            <CardDescription className="text-danger-foreground/80">
              {result.reason}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-danger-foreground/80">
              Check that{" "}
              <code className="rounded bg-danger-border/40 px-1 py-0.5 font-mono text-xs">
                NEXT_PUBLIC_API_BASE_URL
              </code>{" "}
              points at a running deployment, then reload this page.
            </p>
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  const isHealthy = result.kind === "healthy";
  const { data } = result;

  return (
    <PageShell>
      <div className="flex flex-col gap-2">
        <Eyebrow>Tether status</Eyebrow>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          {isHealthy ? "API: healthy · Database: connected" : "Database unavailable"}
        </h1>
        <p className="max-w-md text-sm text-muted-foreground">
          {isHealthy
            ? "The API is reachable and the database is responding to queries."
            : "The API is reachable, but it could not reach the database."}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-3">
        <StatCell label="API" value="Reachable" tone="neutral" />
        <StatCell
          label="Database"
          value={data.db === "connected" ? "Connected" : "Error"}
          tone={data.db === "connected" ? "neutral" : "warn"}
        />
        <StatCell label="Version" value={data.version} tone="neutral" mono />
      </div>
    </PageShell>
  );
}

function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto flex w-full max-w-xl flex-col gap-6">{children}</div>;
}

function StatCell({
  label,
  value,
  tone,
  mono = false,
}: {
  label: string;
  value: string;
  tone: BadgeProps["tone"];
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-2 bg-card p-5">
      <span className="font-mono text-xs tracking-[0.15em] text-muted-foreground uppercase">
        {label}
      </span>
      <Badge tone={tone} className={mono ? "font-mono tracking-normal normal-case" : undefined}>
        {value}
      </Badge>
    </div>
  );
}
